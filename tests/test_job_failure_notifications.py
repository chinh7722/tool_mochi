"""Offline private operator failure notifications and credential-free causes."""
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import Mock,patch
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
sys.path.insert(0,str(ROOT/'tests'))
os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from core.job_failure import job_failure_reason
from core.telegram_users import TelegramUserStore
from core.telegram_bot import TelegramJobRequest,TelegramCustomerRequest
from core.platforms.mochichat.runners.invite_referral import MochiInviteJobSummary,MochiAccountResult
from gui.pages.mochi_chat import MochiChatPage,MochiWorkerThread
from gui.pages.telegram import TelegramPage
import test_telegram_user_ui as fixtures


def summary(message='',*,status='failed',results=(),job='job1'):
    return MochiInviteJobSummary(job,'TEST',10,3,2,1,0,status,results,error_message=message)


class ReasonTests(unittest.TestCase):
    def test_actual_proxy_mail_google_and_invite_causes_are_described(self):
        cases=(('Proxy curl: (28) Connection timed out','thời gian chờ'),
            ('Proxy curl: (35) BoringSSL SSL_connect failed','HTTPS/TLS'),
            ('Tab Proxy mua mail chưa có proxy xoay','Chưa cấu hình proxy'),
            ('Hết thời gian chờ proxy mua mail xoay IP','xoay IP'),
            ('Không cấp được proxy: rotation_not_ready','xoay IP'),
            ('Không có sản phẩm hợp lệ: tất cả đều hết hàng','hết hàng'),
            ('Shop: Không đủ số lượng','không đủ số lượng'),
            ('HTTP 503 Service unavailable','Máy chủ'),('curl: (16) HTTP/2 protocol error','giao thức'),
            ('Shop: Số dư không đủ','Số dư'),('Google CAPTCHA required','CAPTCHA'),
            ('Google account disabled','vô hiệu hóa'),('Google wrong password','thông tin đăng nhập'),
            ('INVITE_LIMIT_REACHED rolling 24-hour','24 giờ'),('INVALID_CODE','Mã mời'),
            ('Đã hết tài khoản đầu vào trước khi đạt mục tiêu','không tự mua bù'),
            ('Không xác định được trạng thái giao dịch mua mail','đối soát'))
        for text,expected in cases:
            with self.subTest(text=text):
                self.assertIn(expected,job_failure_reason(message=text))

    def test_failed_account_results_are_used_when_summary_cause_is_missing(self):
        result=MochiAccountResult('private-mail@example.test',False,'auth_failed',message='Unknown reply')
        reason=job_failure_reason(summary(results=(result,)))
        self.assertIn('Đăng nhập Google/Mochi',reason)
        self.assertNotIn('private-mail',reason)

    def test_repeated_account_failures_are_deduplicated_and_combined_with_batch_cause(self):
        result=MochiAccountResult('private',False,'network_error',message='Connection refused')
        reason=job_failure_reason(summary('Đã hết tài khoản đầu vào',results=(result,)*20))
        self.assertEqual(reason.count('Lô mail'),1)
        self.assertEqual(reason.count('Không kết nối'),1)

    def test_raw_secrets_urls_addresses_and_arbitrary_exception_text_never_leave_tool(self):
        texts=('Proxy curl: (35) SSL_connect failed http://user:super-secret@127.0.0.1:1234 '
            'https://shop.test/api?api_key=api-secret password=mail-secret email=someone@example.test',
            'Arbitrary exception with private-value api_key=api-secret')
        for text in texts:
            reason=job_failure_reason(message=text)
            for private in ('super-secret','api-secret','mail-secret','someone@example.test','127.0.0.1','https://'):
                self.assertNotIn(private,reason)

    def test_unknown_or_missing_reason_does_not_invent_a_specific_failure(self):
        self.assertIn('chưa phân loại',job_failure_reason(message='Unknown provider error'))
        self.assertIn('chưa trả chi tiết',job_failure_reason(summary()))


class NotificationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    setUp=fixtures.TelegramUserUiTests.setUp
    fake_worker=fixtures.TelegramUserUiTests.fake_worker
    remote_job=fixtures.TelegramUserUiTests.remote_job

    def finish(self,job,message='Proxy curl: (28) Connection timed out',status='failed',results=()):
        self.mochi._on_job_finished(summary(message,job=job,status=status,results=results))

    def test_admin_and_partner_receive_cause_only_for_their_own_job(self):
        for index,role in enumerate(('admin','partner'),1):
            self.store.upsert_user(123,'Operator',role)
            job=self.remote_job(index)
            worker=self.page.worker
            worker.reply.reset_mock()
            self.finish(job)
            self.assertEqual(worker.reply.call_args.args[0],123)
            text=worker.reply.call_args.args[1]
            self.assertIn('Nguyên nhân:',text)
            self.assertIn('thời gian chờ',text)
            self.assertIn('Thành công: 2/10',text)
            self.assertNotIn('public',worker.reply.call_args.kwargs)

    def test_exception_signal_also_notifies_the_safe_cause(self):
        job=self.remote_job()
        self.page.worker.reply.reset_mock()
        self.mochi._on_job_error('Proxy curl: (35) SSL_connect failed api_key=secret-value',job)
        text=self.page.worker.reply.call_args.args[1]
        self.assertIn('Nguyên nhân:',text)
        self.assertIn('HTTPS/TLS',text)
        self.assertNotIn('secret-value',text)

    def test_missing_top_level_reason_falls_back_to_actual_account_failure(self):
        job=self.remote_job()
        self.page.worker.reply.reset_mock()
        result=MochiAccountResult('secret@example.test',False,'already_redeemed',message='Account already redeemed')
        self.finish(job,'',results=(result,))
        text=self.page.worker.reply.call_args.args[1]
        self.assertIn('đã nhập mã mời',text)
        self.assertNotIn('secret@example.test',text)

    def test_completed_or_cancelled_job_has_no_failure_cause(self):
        for index,status in enumerate(('completed','cancelled'),1):
            job=self.remote_job(index)
            self.page.worker.reply.reset_mock()
            self.finish(job,'Stale previous failure',status)
            self.assertNotIn('Nguyên nhân:',self.page.worker.reply.call_args.args[1])

    def test_revoked_or_other_bot_recipient_does_not_receive_failure_details(self):
        job=self.remote_job()
        worker=self.page.worker
        worker.reply.reset_mock()
        self.store.set_active(123,False)
        self.finish(job)
        worker.reply.assert_not_called()

    def test_repeated_summary_does_not_send_failure_reason_twice(self):
        job=self.remote_job()
        self.page.worker.reply.reset_mock()
        self.finish(job)
        self.finish(job)
        self.page.worker.reply.assert_called_once()

    def test_customer_result_keeps_its_existing_public_notification(self):
        worker=self.fake_worker()
        self.settings.setValue('finance/sale_rate_per_100','1000')
        self.store.ensure_customer(456,'Customer')
        self.store.set_customer_balance(456,1000000,'Fixture',expected_units=0)
        request=TelegramCustomerRequest(1,456,'job','Customer','TEST',1000)
        with patch.object(MochiWorkerThread,'start'):
            self.page._on_customer_requested((worker.fingerprint,request))
        job=self.store.lookup_request(worker.fingerprint,1).job_id
        worker.reply.reset_mock()
        self.finish(job)
        self.assertTrue(worker.reply.call_args.kwargs['public'])
        self.assertNotIn('Nguyên nhân:',worker.reply.call_args.args[1])

    def test_configuration_rejection_explains_why_request_cannot_create_job(self):
        worker=self.fake_worker()
        with self.store._connection(write=True) as db:
            db.execute('DELETE FROM user_mail_keys WHERE chat_id=123')
        self.mochi.input_api_key.setText('shared-key-must-not-be-used')
        self.page._on_job_requested((worker.fingerprint,TelegramJobRequest(1,123,'TEST',1000)))
        text=worker.reply.call_args.args[1]
        self.assertIn('Nguyên nhân:',text)
        self.assertIn('API key',text)
        self.assertFalse(self.mochi._workers)

    def test_worker_start_failure_uses_the_actual_exception_cause(self):
        worker=self.fake_worker()
        with patch.object(MochiWorkerThread,'start',side_effect=RuntimeError('Browser executable not found')):
            self.page._on_job_requested((worker.fingerprint,TelegramJobRequest(1,123,'TEST',1000)))
        worker.reply.assert_called_once()
        self.assertIn('Trình duyệt',worker.reply.call_args.args[1])


if __name__=='__main__':
    unittest.main(verbosity=2)
