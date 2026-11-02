"""Offline Telegram-to-job-to-purchase accounting integration."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication, QDialog, QLineEdit, QPushButton, QTableWidget, QTabWidget
from core.finance import MailPurchaseReceipt, money_units
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary
from core.telegram_bot import TelegramAccess, TelegramJobRequest
from gui.pages.mochi_chat import MochiChatPage, MochiWorkerThread
from gui.pages.telegram import TelegramPage


class FinanceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings = QSettings(str(Path(self.temp.name) / 'settings.ini'), QSettings.IniFormat)
        self.mochi = MochiChatPage(settings=self.settings)
        self.mochi.input_api_key.setText('offline-not-a-real-key')
        self.page = TelegramPage(self.settings, self.mochi)
        self.addCleanup(self.mochi.close)
        self.addCleanup(self.page.close)
        self.page.store.upsert_user(123, 'Đối tác A')
        self.page.store.upsert_user(456, 'Đối tác B')
        for chat_id in (123,456):
            self.page.store.set_user_mail_keys(chat_id,f'offline-gmailvip-{chat_id}',f'offline-1trieumail-{chat_id}')
        self.worker = SimpleNamespace(fingerprint=hashlib.sha256(b'offline').hexdigest(),
            access=TelegramAccess((123, 456)), reply=Mock(), complete_request=Mock())
        self.page.worker = self.worker
        self.page._accepting_requests = True
        self.page._render_users()

    def price(self, value):
        self.page.input_sale_rate.setText(value)
        self.page.btn_save_finance.click()

    def job(self, update=1, chat=123):
        def verify_owner_before_start():
            self.assertIsNotNone(self.page.store.lookup_request(self.worker.fingerprint, update))
        with patch.object(MochiWorkerThread, 'start', side_effect=verify_owner_before_start):
            self.page._on_job_requested((self.worker.fingerprint, TelegramJobRequest(update, chat, 'TEST', 1000, '')))
        stored = self.page.store.lookup_request(self.worker.fingerprint, update)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.status, 'running')
        return stored.job_id

    def buy(self, job, cost, pid='purchase1'):
        receipt = MailPurchaseReceipt(pid, job, 'shop.test', 'safe-hash', 1, 10,
            None if cost is None else money_units(cost))
        runner = self.mochi._workers[job].runner
        self.assertTrue(callable(runner.mail_purchase_sink))
        runner.mail_purchase_sink(receipt)
        self.app.processEvents()

    def finish(self, job, status='completed', successes=10):
        self.mochi._on_job_finished(MochiInviteJobSummary(job, 'TEST', 10,
            successes, successes, 0, 0, status, ()))

    def row(self, chat=123):
        return next(row for row in range(self.page.table_users.rowCount())
            if self.page.table_users.item(row, 0).text() == str(chat))

    def test_global_rate_is_snapshotted_before_worker_starts_and_only_new_jobs_use_new_rate(self):
        self.price('1000.25')
        first = self.job()
        self.price('2000')
        second = self.job(2)
        self.assertEqual(self.page.store.get_job(first).sale_rate_units, 100025)
        self.assertEqual(self.page.store.get_job(second).sale_rate_units, 200000)
        self.finish(first)
        self.finish(second)
        self.assertEqual(str(self.page.store.get_user(123).revenue_vnd), '30002.5')

    def test_purchase_sink_books_full_batch_for_the_correct_user_before_any_success(self):
        self.price('1000')
        mine, other = self.job(), self.job(2, 456)
        self.buy(mine, '500.65')
        self.assertEqual(self.page.store.get_user(123).cost_units, 50065)
        self.assertEqual(self.page.store.get_user(456).cost_units, 0)
        row = self.row()
        self.assertEqual(self.page.table_users.item(row, 8).text(), '500.65')
        self.assertEqual(self.page.table_users.item(row, 9).text(), '-500.65')
        self.finish(mine)
        self.finish(other, 'failed', 0)
        self.assertEqual(self.page.table_users.item(self.row(), 7).text(), '10,000')
        self.assertEqual(self.page.table_users.item(self.row(), 9).text(), '9,499.35')

    def test_failed_partial_job_has_cost_and_beans_but_no_order_revenue(self):
        self.price('1000')
        job = self.job()
        self.buy(job, '300')
        self.finish(job, 'failed', 2)
        user = self.page.store.get_user(123)
        self.assertEqual((user.sold_beans, user.revenue_units, user.cost_units), (200, 0, 30000))

    def test_unresolved_cost_never_displays_a_final_profit_and_can_be_reconciled_in_dialog(self):
        self.price('1000')
        job = self.job()
        self.buy(job, None)
        self.finish(job)
        self.assertEqual(self.page.table_users.item(self.row(), 9).text(), 'Chờ đối soát')
        with patch('gui.pages.telegram.QDialog.exec', return_value=0):
            self.page.btn_finance_ledger.click()
        dialog = self.page.findChildren(QDialog)[-1]
        tabs = dialog.findChild(QTabWidget)
        costs = tabs.widget(0)
        table = costs.findChild(QTableWidget)
        table.selectRow(0)
        costs.findChild(QLineEdit).setText('123.65')
        next(b for b in costs.findChildren(QPushButton) if b.text() == 'Xác nhận chi phí').click()
        self.assertEqual(self.page.store.get_user(123).cost_units, 12365)
        self.assertEqual(self.page.table_users.item(self.row(), 9).text(), '9,876.35')

    def test_own_stats_include_money_without_exposing_another_user(self):
        self.price('1000')
        job = self.job()
        self.buy(job, '300')
        self.finish(job)
        stats = self.page._user_stats_text(self.page.store.get_user(123))
        self.assertIn('Doanh thu: 10,000 VND', stats)
        self.assertIn('Chi phí mail: 300 VND', stats)
        self.assertIn('Lợi nhuận: 9,700 VND', stats)
        self.assertNotIn('Đối tác B', stats)

    def test_unset_rate_is_marked_missing_and_can_be_added_to_that_job_only(self):
        job = self.job()
        self.finish(job)
        self.assertEqual(self.page.store.get_user(123).unpriced_jobs, 1)
        self.assertEqual(self.page.table_users.item(self.row(), 9).text(), 'Chờ đối soát')
        with patch('gui.pages.telegram.QDialog.exec', return_value=0):
            self.page.btn_finance_ledger.click()
        prices = self.page.findChildren(QDialog)[-1].findChild(QTabWidget).widget(1)
        prices.findChild(QTableWidget).selectRow(0)
        prices.findChild(QLineEdit).setText('1000')
        next(b for b in prices.findChildren(QPushButton) if b.text() == 'Bổ sung giá cho job').click()
        self.assertEqual(self.page.store.get_user(123).revenue_units, 1000000)
        self.assertIsNone(self.settings.value('finance/sale_rate_per_100'))

    def test_invalid_rate_does_not_overwrite_saved_price_and_zero_is_supported(self):
        self.price('123.45')
        self.price('-1')
        self.assertEqual(self.page._sale_rate_snapshot(), 12345)
        self.price('0')
        self.assertEqual(self.page._sale_rate_snapshot(), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
