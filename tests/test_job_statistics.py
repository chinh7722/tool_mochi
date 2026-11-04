"""Per-day financial totals, durable combo customer counts and child-job holds."""
from datetime import datetime,timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from core.telegram_users import TelegramUserStore
from core.finance import MailPurchaseReceipt


class StatisticsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=TelegramUserStore(Path(self.temp.name)/'ledger.sqlite3')
        self.store.upsert_user(100,'Admin','admin');self.store.upsert_user(200,'Partner','partner')

    def job(self,jid,owner=100,*,status='completed',success=2,rate=100025,cost=10050,day='2026-11-04T03:00:00+00:00'):
        self.store.record_job(jid,owner,'SAMECODE',500,5,sale_rate_units=rate)
        self.store.record_purchase(MailPurchaseReceipt('p-'+jid,jid,'shop','hash',1,2,cost))
        self.store.record_job_run(jid,'r1',status,success,success+1,'Đăng nhập Google lỗi',finished_at=day)
        self.store.finish_job(jid,status,success,failure_count=1,attempted_count=success+1)
        with self.store._connection(write=True) as db:db.execute('UPDATE jobs SET finalized_at=? WHERE job_id=?',(day,jid))

    def test_money_totals_daily_and_people_use_exact_units(self):
        self.job('a');self.job('b',200,status='failed',success=1,cost=2555)
        report=self.store.statistics('2026-11',day='2026-11-04')
        day=report['days'][3]
        self.assertEqual((day['jobs'],day['revenue_units'],day['cost_units'],day['profit_units']),(2,200050,12605,187445))
        self.assertEqual(len(report['users']),2)
        self.assertEqual(report['total'],2)

    def test_unknown_cost_and_unpriced_revenue_not_presented_as_full_profit(self):
        self.job('pending',cost=None);self.job('unpriced',rate=None)
        report=self.store.statistics('2026-11')
        self.assertEqual(report['days'][3]['pending_jobs'],2)
        self.assertTrue(all(r['profit_units'] is None for r in report['history']))

    def test_vietnam_midnight_moves_job_to_next_day(self):
        self.job('midnight',day='2026-11-03T18:05:00+00:00')
        report=self.store.statistics('2026-11',day='2026-11-04')
        self.assertEqual(report['total'],1)
        self.assertEqual(report['days'][2]['jobs'],0)

    def test_combo_each_round_is_one_customer_but_job_count_only_on_final(self):
        self.store.record_job('combo',100,'SAMECODE',5500,55)
        self.store.record_job_run('combo','day1','invite_limit_reached',10,10,finished_at='2026-11-03T09:00:00+07:00')
        self.store.record_job_run('combo','day2','failed',12,15,'Google lỗi',finished_at='2026-11-04T09:00:00+07:00')
        report=self.store.statistics('2026-11')
        self.assertEqual([report['days'][i]['customers'] for i in (2,3)],[1,1])
        self.assertEqual(sum(r['jobs'] for r in report['days']),0)
        self.assertEqual(report['customer_total'],2)

    def test_run_replay_restart_and_legacy_seed_never_double_count(self):
        self.job('a')
        self.assertFalse(self.store.record_job_run('a','r1','completed',2,3))
        reopened=TelegramUserStore(self.store.path)
        self.assertEqual(reopened.statistics('2026-11')['customer_total'],1)

    def test_legacy_finalized_job_is_seeded_once_and_no_combo_rounds_invented(self):
        self.store.record_job('old',100,'CODE',100,1);self.store.finish_job('old','failed',0)
        with self.store._connection(write=True) as db:db.execute("UPDATE jobs SET finalized_at='2026-11-03T12:00:00+07:00'")
        reopened=TelegramUserStore(self.store.path)
        report=reopened.statistics('2026-11')
        self.assertEqual(report['customer_total'],1)
        self.assertEqual(report['customer_history'][0]['reason'],'Chưa ghi nhận lý do ở phiên cũ')

    def test_owner_filter_pagination_and_month_boundaries(self):
        self.job('a');self.job('b',200);self.job('other-month',day='2026-10-31T00:00:00Z')
        report=self.store.statistics('2026-11',owner_id=200,limit=1)
        self.assertEqual((report['total'],report['history'][0]['chat_id']),(1,200))
        self.assertEqual(len(report['days']),30)
        self.assertEqual(len(self.store.statistics('2028-02')['days']),29)
        for options in ({'day':'2026-10-31'},{'owner_id':0},{'offset':-1},{'limit':501}):
            with self.assertRaises(ValueError):self.store.statistics('2026-11',**options)

    def test_reasons_percent_denominator_excludes_cancel_and_combo_quota(self):
        self.job('failed',status='failed');self.job('cancel',status='cancelled')
        self.store.record_job('combo',100,'CODE',2000,20)
        self.store.record_job_run('combo','r','invite_limit_reached',10,10,finished_at='2026-11-04T09:00:00+07:00')
        report=self.store.statistics('2026-11')
        self.assertEqual(sum(r['count'] for r in report['reasons']),1)

    def test_continuation_copies_owner_rate_and_only_missing_target_without_mutating_source(self):
        self.job('failed',owner=200,status='failed',success=2)
        self.store.create_continuation('failed','child',scheduled_at='2026-11-04T09:00:00+07:00',schedule_data={})
        child=self.store.get_job('child');source=self.store.get_job('failed')
        self.assertEqual((child.chat_id,child.target_count,child.requested_beans,child.sale_rate_units),(200,3,300,100025))
        self.assertEqual((source.status,source.success_count),('failed',2))
        self.assertEqual(self.store.continuation('failed'),'child')
        with self.assertRaises(ValueError):self.store.create_continuation('failed','duplicate',scheduled_at=None,schedule_data={})
        self.assertIsNone(self.store.get_job('duplicate'))

    def test_continuation_denies_completed_active_and_revoked_owner(self):
        self.job('completed')
        with self.assertRaises(ValueError):self.store.create_continuation('completed','new',scheduled_at=None,schedule_data={})
        self.job('revoked',200,status='failed');self.store.set_active(200,False)
        with self.assertRaises(ValueError):self.store.create_continuation('revoked','new',scheduled_at=None,schedule_data={})

    def test_customer_continuation_holds_only_missing_amount_and_settles_once(self):
        s=self.store
        s.ensure_customer(300,'Khách');s.set_customer_balance(300,1000000,'fund',expected_units=0)
        s.record_customer_order('order',300,'CODE',500,5,'bot',1,100000)
        s.finish_job('order','failed',2,3,5)
        self.assertEqual(s.get_customer(300)['balance_units'],800000)
        s.create_continuation('order','child',scheduled_at='2026-11-04T12:00:00+07:00',schedule_data={})
        self.assertEqual(s.get_customer(300)['balance_units'],500000)
        s.finish_job('child','completed',3,0,3);s.finish_job('child','completed',3,0,3)
        self.assertEqual(s.get_customer(300)['balance_units'],500000)
        self.assertEqual(s.get_customer_order_for_job('child')['charged_units'],300000)

    def test_insufficient_customer_wallet_denies_continuation_without_job_or_hold(self):
        s=self.store
        s.ensure_customer(300,'Khách');s.set_customer_balance(300,500000,'fund',expected_units=0)
        s.record_customer_order('order',300,'CODE',500,5,'bot',1,100000)
        s.finish_job('order','failed',2,3,5)
        s.set_customer_balance(300,0,'withdraw',expected_units=300000)
        with self.assertRaises(ValueError):s.create_continuation('order','child',scheduled_at=None,schedule_data={})
        self.assertIsNone(s.get_job('child'))
        self.assertIsNone(s.continuation('order'))

    def test_customer_revenue_uses_actual_settlement_and_costs_once(self):
        s=self.store
        s.ensure_customer(300,'Khách');s.set_customer_balance(300,500000,'fund',expected_units=0)
        s.record_customer_order('order',300,'CODE',500,5,'bot',1,100000)
        s.record_purchase(MailPurchaseReceipt('mail','order','shop','hash',1,5,55000))
        s.finish_job('order','failed',2,3,5)
        with s._connection(write=True) as db:db.execute("UPDATE jobs SET finalized_at='2026-11-04T09:00:00+07:00'")
        r=s.statistics('2026-11')['history'][0]
        self.assertEqual((r['revenue_units'],r['cost_units'],r['profit_units']),(200000,55000,145000))


if __name__=='__main__':unittest.main(verbosity=2)
