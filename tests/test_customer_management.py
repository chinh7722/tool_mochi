"""Offline customer classification, deletion and audited wallet corrections."""
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
os.environ['QT_QPA_PLATFORM']='offscreen'
import test_customers_sepay as fixtures
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication,QDialog,QLineEdit,QPushButton,QLabel
from core.finance import MAX_MONEY_UNITS
from core.telegram_users import TelegramUserStore
from core.telegram_bot import TelegramBotService,TelegramAccess,TelegramCustomerRequest
from gui.pages.mochi_chat import MochiChatPage
from gui.pages.telegram import TelegramPage


class CustomerStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path=Path(self.temp.name)/'accounts.sqlite3'
        self.store=TelegramUserStore(self.path)
        self.store.ensure_customer(456,'Khách A')

    def deposit(self,amount=10000):
        data=fixtures.transaction(amount)
        self.assertTrue(self.store.process_sepay_transaction(data,fixtures.BANK,
            fixtures.CONFIG.account_number,fixtures.START))
        return data

    def order(self,job='job1',update=1,count=10,rate=100000):
        self.assertTrue(self.store.record_customer_order(job,456,'TEST',count*100,count,'fp',update,rate))

    def test_active_admin_and_partner_cannot_be_registered_or_managed_as_customers(self):
        for role in ('admin','partner'):
            self.store.upsert_user(456,'Đã cấp quyền',role)
            self.assertEqual(self.store.list_customers(),())
            for operation in (lambda:self.store.ensure_customer(456),lambda:self.store.ensure_customer(456,restore=True),
                    lambda:self.store.delete_customer(456),lambda:self.store.set_customer_balance(456,100,'Test',expected_units=0)):
                with self.assertRaises(ValueError):
                    operation()
        self.store.upsert_user(789,'Admin mới','admin')
        with self.assertRaises(ValueError):
            self.store.ensure_customer(789)
        self.assertIsNone(self.store.get_customer(789))
        self.store.set_active(789,False)
        self.store.sync_customer_profiles()
        self.assertEqual([r['chat_id'] for r in self.store.list_customers()],[789])

    def test_edit_balance_up_and_down_has_exact_delta_before_after_and_reason(self):
        self.store.set_customer_balance(456,5000025,'Nạp bổ sung',expected_units=0)
        self.store.set_customer_balance(456,1000050,'Điều chỉnh lại',expected_units=5000025)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000050)
        self.assertFalse(self.store.set_customer_balance(456,1000050,'Không thay đổi',expected_units=1000050))
        history=self.store.customer_history(456)
        self.assertEqual(len(history),2)
        self.assertEqual({r['kind'] for r in history},{'adjustment'})
        self.assertEqual(sum(r['amount_units'] for r in history),1000050)
        self.assertIn('Điều chỉnh lại',history[0]['description'])
        with self.store._connection() as db:
            row=db.execute('SELECT before_units,after_units FROM customer_adjustments ORDER BY created_at DESC').fetchone()
        self.assertEqual(tuple(row),(5000025,1000050))
        self.assertEqual(TelegramUserStore(self.path).get_customer(456)['balance_units'],1000050)

    def test_invalid_balance_or_empty_reason_never_changes_balance(self):
        for target,reason in ((-1,'Test'),(True,'Test'),(1.5,'Test'),(MAX_MONEY_UNITS+1,'Test'),(1,''),(1,'x'*301)):
            with self.subTest(target=target),self.assertRaises(ValueError):
                self.store.set_customer_balance(456,target,reason,expected_units=0)
        self.assertEqual(self.store.customer_history(456),())

    def test_adjusted_balance_is_used_by_orders_deposits_and_refunds(self):
        self.store.set_customer_balance(456,2000000,'Số dư ban đầu',expected_units=0)
        self.order()
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.deposit()
        self.store.finish_job('job1','failed',3)
        self.assertEqual(self.store.get_customer(456)['balance_units'],2700000)
        self.assertEqual(self.store.get_user(456).revenue_units,300000)

    def test_new_deposit_during_edit_prevents_overwriting_current_balance(self):
        self.deposit()
        with self.assertRaisesRegex(ValueError,'đã thay đổi'):
            self.store.set_customer_balance(456,5000000,'Stale edit',expected_units=0)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)
        self.assertEqual(len(self.store.customer_history(456)),1)

    def test_two_simultaneous_edits_cannot_silently_overwrite_each_other(self):
        def edit(target):
            try:
                return self.store.set_customer_balance(456,target,'Concurrent edit',expected_units=0)
            except ValueError:
                return False
        with ThreadPoolExecutor(max_workers=2) as executor:
            results=list(executor.map(edit,[1000000,2000000]))
        self.assertEqual(sum(results),1)
        self.assertEqual(len(self.store.customer_history(456)),1)

    def test_balance_edit_while_an_order_is_running_preserves_the_hold(self):
        self.deposit()
        self.order()
        self.store.set_customer_balance(456,50000,'Chỉnh tiền khả dụng',expected_units=0)
        self.assertEqual(self.store.get_customer_order_for_job('job1')['reserved_units'],1000000)
        self.store.finish_job('job1','failed',3)
        self.assertEqual(self.store.get_customer(456)['balance_units'],750000)

    def test_deletion_restore_preserve_money_orders_and_transaction_dedup(self):
        data=self.deposit()
        self.order()
        self.store.finish_job('job1','failed',3)
        self.assertTrue(self.store.delete_customer(456))
        self.assertFalse(self.store.delete_customer(456))
        self.assertEqual(self.store.list_customers(),())
        self.store.sync_customer_profiles()
        self.assertEqual(TelegramUserStore(self.path).list_customers(),())
        with self.assertRaises(ValueError):
            self.store.ensure_customer(456)
        self.store.ensure_customer(456,restore=True)
        self.assertEqual(self.store.get_customer(456)['balance_units'],700000)
        self.assertEqual(len(self.store.customer_orders(456)),1)
        self.assertFalse(self.store.process_sepay_transaction(data,fixtures.BANK,fixtures.CONFIG.account_number,fixtures.START))
        kinds={r['kind'] for r in self.store.customer_history(456)}
        self.assertTrue({'customer_deleted','customer_restored','deposit','reserve','refund'}<=kinds)

    def test_customer_with_unsettled_order_cannot_be_deleted(self):
        self.deposit()
        self.order()
        with self.assertRaisesRegex(ValueError,'chưa chốt'):
            self.store.delete_customer(456)
        self.assertEqual(len(self.store.list_customers()),1)

    def test_deleted_customer_cannot_receive_automatic_deposit_edit_or_new_order(self):
        self.store.delete_customer(456)
        data=fixtures.transaction()
        self.assertFalse(self.store.process_sepay_transaction(data,fixtures.BANK,fixtures.CONFIG.account_number,fixtures.START))
        for operation in (lambda:self.store.set_customer_balance(456,100,'Edit deleted',expected_units=0),
                lambda:self.store.record_customer_order('job1',456,'TEST',100,1,'fp',1,100)):
            with self.assertRaises(ValueError):
                operation()
        self.store.ensure_customer(456,restore=True)
        self.store.reconcile_sepay_credit(data['id'],456)
        self.assertEqual(self.store.get_customer(456)['balance_units'],1000000)

    def test_authorized_id_is_never_credited_by_sepay_or_manual_matching(self):
        self.store.upsert_user(456,'Admin','admin')
        data=fixtures.transaction()
        self.assertFalse(self.store.process_sepay_transaction(data,fixtures.BANK,fixtures.CONFIG.account_number,fixtures.START))
        unmatched=fixtures.transaction(content='Unknown deposit')
        self.store.process_sepay_transaction(unmatched,fixtures.BANK,fixtures.CONFIG.account_number,fixtures.START)
        with self.assertRaises(ValueError):
            self.store.reconcile_sepay_credit(unmatched['id'],456)
        self.assertEqual(self.store.get_customer(456)['balance_units'],0)

    def test_large_lifetime_turnover_preserves_exact_available_balance(self):
        self.store.set_customer_balance(456,MAX_MONEY_UNITS,'Initial',expected_units=0)
        self.order(count=1,rate=100)
        self.store.set_customer_balance(456,MAX_MONEY_UNITS,'Restore available',expected_units=MAX_MONEY_UNITS-100)
        self.assertEqual(self.store.get_customer(456)['balance_units'],MAX_MONEY_UNITS)

    def test_schema_upgrade_preserves_existing_wallet_and_order(self):
        data=self.deposit()
        self.order()
        self.store.finish_job('job1','failed',2)
        # Recreate the schema shape shipped before customer management existed.
        with self.store._connection(write=True) as db:
            db.execute('ALTER TABLE customers DROP COLUMN deleted_at')
            db.execute('DROP TABLE customer_adjustments')
            db.execute('DROP TABLE customer_events')
        upgraded=TelegramUserStore(self.path)
        self.assertEqual(upgraded.get_customer(456)['balance_units'],800000)
        self.assertEqual(len(upgraded.customer_orders(456)),1)
        self.assertFalse(upgraded.process_sepay_transaction(data,fixtures.BANK,fixtures.CONFIG.account_number,fixtures.START))


class CustomerManagementUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.settings=QSettings(str(Path(self.temp.name)/'settings.ini'),QSettings.IniFormat)
        self.mochi=MochiChatPage(settings=self.settings)
        self.page=TelegramPage(self.settings,self.mochi)
        self.addCleanup(self.page.close)
        self.addCleanup(self.mochi.close)
        self.store=self.page.store
        self.worker=SimpleNamespace(fingerprint='fp',access=TelegramAccess(),reply=Mock(),complete_request=Mock())
        self.page.worker=self.worker
        self.page._accepting_requests=True
        self.customers=self.page.customer_page

    def auto_register(self):
        self.page._on_customer_requested(('fp',TelegramCustomerRequest(1,456,'help','Khách tự động')))

    def test_new_customer_appears_without_using_the_manual_add_action(self):
        self.auto_register()
        self.assertEqual(self.customers.table.rowCount(),1)
        self.assertEqual(self.customers.table.item(0,0).text(),'456')
        self.assertEqual(self.customers.input_id.text(),'')

    def test_manual_add_or_restore_cannot_insert_active_admin_partner(self):
        for role in ('admin','partner'):
            self.store.upsert_user(123,'Được cấp quyền',role)
            self.customers.input_id.setText('123')
            self.customers.add_customer()
            self.assertEqual(self.customers.table.rowCount(),0)
            self.assertIsNone(self.store.get_customer(123))
        self.assertFalse(hasattr(self.customers,'show_authorized'))

    def test_promotion_removes_customer_and_revocation_restores_classification(self):
        self.auto_register()
        self.store.upsert_user(456,'Partner','partner')
        self.page._sync_access()
        self.assertEqual(self.customers.table.rowCount(),0)
        self.store.set_active(456,False)
        self.page._sync_access()
        self.assertEqual(self.customers.table.rowCount(),1)

    def test_delete_and_restore_buttons_keep_the_original_wallet(self):
        self.auto_register()
        self.customers.save_balance(456,'25000.75','Số dư ban đầu',0)
        self.customers.table.selectRow(0)
        self.assertTrue(self.customers.btn_delete.isEnabled())
        self.customers.btn_delete.click()
        self.assertEqual(self.customers.table.rowCount(),0)
        self.assertFalse(self.customers.btn_edit_balance.isEnabled())
        self.customers.btn_add.click()
        self.assertEqual(self.customers.table.rowCount(),1)
        self.assertEqual(self.store.get_customer(456)['balance_units'],2500075)

    def test_balance_dialog_commits_the_selected_customer_and_saves_audit(self):
        self.auto_register()
        self.customers.table.selectRow(0)
        def edit(dialog):
            fields=dialog.findChildren(QLineEdit)
            fields[0].setText('50000.25')
            fields[1].setText('Bù số dư cho khách')
            next(button for button in dialog.findChildren(QPushButton) if button.text()=='Lưu số dư').click()
            return dialog.result()
        with patch.object(QDialog,'exec',edit):
            self.customers.btn_edit_balance.click()
        self.assertEqual(self.store.get_customer(456)['balance_units'],5000025)
        self.assertIn('Bù số dư cho khách',self.store.customer_history(456)[0]['description'])
        self.assertIn('50,000.25',self.customers.table.item(0,1).text())

    def test_deleted_customer_message_does_not_recreate_the_profile(self):
        self.auto_register()
        self.store.delete_customer(456)
        self.auto_register()
        self.assertEqual(self.customers.table.rowCount(),0)
        self.assertIn('đã bị xóa',self.worker.reply.call_args.args[1])

    def test_non_text_private_message_registers_without_replying_or_granting_access(self):
        service=TelegramBotService(None,TelegramAccess([123]),Mock(),
            on_customer=lambda request:self.page._on_customer_requested(('fp',request)))
        update={'update_id':2,'message':{'chat':{'id':456,'type':'private'},
            'from':{'id':456,'first_name':'Ảnh'},'photo':[{}]}}
        service.handle_update(update)
        self.assertEqual(self.store.get_customer(456)['name'],'Ảnh')
        self.assertEqual(self.customers.table.rowCount(),1)
        self.worker.reply.assert_not_called()
        self.worker.complete_request.assert_called_once_with(2)
        self.assertEqual(self.store.active_ids(),())


if __name__=='__main__':
    unittest.main(verbosity=2)
