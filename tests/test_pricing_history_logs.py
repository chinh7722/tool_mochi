"""Offline exact milestone prices, append-only logs and non-destructive history clearing."""
import csv
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from core.telegram_users import TelegramUserStore
from core.pricing import validate_prices,quote_prices,priced_metadata,earned_units
from core.job_log_store import JobLogStore


class PricingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=TelegramUserStore(Path(self.temp.name)/'ledger.sqlite3')
        self.store.upsert_user(123,'Partner')

    def quote(self,points,rows=None):
        return quote_prices(rows or [dict(beans=2000,price_units=1500000)],points)

    def test_price_scales_below_and_above_milestone(self):
        self.assertEqual([self.quote(p)['total_units'] for p in (1000,2000,3000)], [750000,1500000,2250000])

    def test_multiple_milestones_choose_largest_not_above_target_and_lowest_below_all(self):
        rows=validate_prices([dict(beans=2000,price_units=1500000),dict(beans=1000,price_units=900000)])
        self.assertEqual([self.quote(p,rows)['total_units'] for p in (500,1000,1500,2000,2500)],
            [450000,900000,1350000,1500000,1875000])

    def test_empty_table_falls_back_to_existing_rate_and_billable_accounts_round_up(self):
        self.assertEqual(quote_prices([],250,100025)['total_units'],300075)
        self.assertEqual(self.quote(2050)['total_units'],1575000)

    def test_fractional_price_does_not_round_each_account_and_lose_total(self):
        quote=quote_prices([dict(beans=5500,price_units=3500000)],5500)
        data=priced_metadata({},quote)
        self.assertEqual(earned_units(55,55,quote['rate_units'],data),3500000)
        self.assertEqual(earned_units(1,55,quote['rate_units'],data),3500000//55)
        self.assertNotEqual(quote['rate_units']*55,3500000)

    def test_invalid_or_duplicate_tiers_are_rejected(self):
        for rows in (None,[dict(beans=0,price_units=100)],[dict(beans=150,price_units=100)],
                [dict(beans=100,price_units=0)],[dict(beans=100,price_units=1)]*2,
                [dict(beans=True,price_units=100)],[dict(beans=100,price_units=2**63)]):
            with self.assertRaises(ValueError):validate_prices(rows)

    def test_operator_report_user_totals_and_statistics_share_exact_snapshot(self):
        quote=quote_prices([dict(beans=5500,price_units=3500000)],5500)
        self.store.record_job('priced',123,'CODE',5500,55,sale_rate_units=quote['rate_units'],schedule_data=priced_metadata({},quote))
        self.store.finish_job('priced','completed',55)
        self.assertEqual(self.store.get_user(123).revenue_units,3500000)
        report=self.store.user_report(123)
        row=next(csv.DictReader(io.StringIO(report['files'][0]['text'].lstrip('\ufeff'))))
        self.assertEqual(row['Doanh thu đã ghi nhận (VND)'],'35000.00')
        self.assertEqual(row['Giá chốt theo bảng giá (VND)'],'35000.00')
        month=self.store.get_job('priced').finalized_at[:7]
        self.assertEqual(self.store.statistics(month)['history'][0]['revenue_units'],3500000)

    def test_customer_partial_charge_and_continuation_conserve_exact_original_total(self):
        s=self.store;s.ensure_customer(456,'Customer');s.set_customer_balance(456,5000000,'fund',expected_units=0)
        quote=quote_prices([dict(beans=5500,price_units=3500000)],5500)
        s.record_customer_order('order',456,'CODE',5500,55,'bot',1,quote['rate_units'],schedule_data=priced_metadata({},quote))
        self.assertEqual(s.get_customer_order_for_job('order')['reserved_units'],3500000)
        s.finish_job('order','failed',13,3,16)
        charged=3500000*13//55
        self.assertEqual(s.get_customer_order_for_job('order')['charged_units'],charged)
        s.create_continuation('order','child',scheduled_at='2099-01-01T09:00:00+07:00',schedule_data={})
        self.assertEqual(s.get_customer_order_for_job('child')['reserved_units'],3500000-charged)
        s.finish_job('child','completed',42)
        self.assertEqual(s.get_customer(456)['balance_units'],1500000)
        self.assertEqual(s.get_user(456).revenue_units,3500000)

    def test_changing_source_table_does_not_reprice_an_existing_job(self):
        original=self.quote(2000)
        self.store.record_job('old',123,'CODE',2000,20,sale_rate_units=original['rate_units'],schedule_data=priced_metadata({},original))
        changed=quote_prices([dict(beans=2000,price_units=2000000)],2000)
        self.assertEqual(changed['total_units'],2000000)
        self.store.finish_job('old','completed',20)
        self.assertEqual(self.store.get_user(123).revenue_units,1500000)


class HistoryAndLogsTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=TelegramUserStore(Path(self.temp.name)/'ledger.sqlite3')
        self.store.upsert_user(123,'Partner');self.store.upsert_user(456,'Admin','admin')

    def job(self,jid,code='CODE',owner=123):
        self.store.record_job(jid,owner,code,100,1,sale_rate_units=100000)

    def test_duplicate_codes_count_one_customer_per_day_across_owners_and_runs(self):
        for jid,code,owner in (('one','code',123),('two','CODE',456),('three','OTHER',123)):
            self.job(jid,code,owner)
            self.store.record_job_run(jid,'r1','failed',0,1,finished_at='2026-11-04T09:00:00+07:00')
        self.store.record_job_run('one','r2','completed',1,1,finished_at='2026-11-04T10:00:00+07:00')
        self.store.record_job_run('one','r3','completed',1,1,finished_at='2026-11-05T10:00:00+07:00')
        r=self.store.statistics('2026-11',day='2026-11-04')
        self.assertEqual((r['days'][3]['customers'],r['days'][3]['unique_customers']),(4,2))
        self.assertEqual(r['days'][4]['unique_customers'],1)
        code=next(c for c in r['code_history'] if c['code']=='CODE')
        self.assertEqual((code['runs'],len(code['owners'])),(3,2))

    def test_all_month_option_includes_jobs_on_different_days(self):
        for jid,day in (('one','04'),('two','05')):
            self.job(jid);self.store.finish_job(jid,'failed',0)
            with self.store._connection(write=True) as db:db.execute('UPDATE jobs SET finalized_at=? WHERE job_id=?',(f'2026-11-{day}T09:00:00+07:00',jid))
        self.assertEqual(self.store.statistics('2026-11',day='2026-11-04')['total'],1)
        self.assertEqual(self.store.statistics('2026-11',day=None)['total'],2)

    def test_clear_temporary_history_never_changes_statistics_money_runs_or_logs(self):
        self.job('done');self.job('running')
        self.store.finish_job('done','failed',0,3,3)
        self.store.record_job_run('done','r1','failed',0,3,finished_at='2026-11-04T09:00:00+07:00')
        before=self.store.statistics('2026-11')
        user=self.store.get_user(123)
        self.assertEqual(self.store.temporary_history()['total'],1)
        self.store.clear_temporary_history()
        self.assertEqual(self.store.temporary_history()['total'],0)
        self.assertEqual(self.store.statistics('2026-11'),before)
        self.assertEqual(self.store.get_user(123),user)
        self.assertIsNotNone(self.store.get_job('done'))
        self.store.finish_job('running','completed',1)
        self.assertEqual([r['job_id'] for r in self.store.temporary_history()['rows']],['running'])

    def test_temporary_clear_survives_reopen_and_failed_jobs_can_continue(self):
        self.job('failed');self.store.finish_job('failed','failed',0)
        self.assertTrue(self.store.temporary_history()['rows'][0]['can_continue'])
        self.store.clear_temporary_history()
        self.assertEqual(TelegramUserStore(self.store.path).temporary_history()['total'],0)

    def test_log_survives_restart_and_appends_same_combo_id_across_rounds(self):
        path=Path(self.temp.name)/'logs.sqlite3'
        first=JobLogStore(path);first.append('combo','Ngày 1: thành công');r=first.read('combo');first.close()
        second=JobLogStore(path)
        try:
            second.append('combo','Ngày 2: tiếp tục')
            new=second.read('combo',r['cursor'],r['epoch'])
            self.assertFalse(new['reset']);self.assertEqual(new['lines'],['Ngày 2: tiếp tục'])
            self.assertEqual(second.read('combo')['lines'],['Ngày 1: thành công','Ngày 2: tiếp tục'])
        finally:second.close()

    def test_log_pagination_returns_every_line_and_isolates_jobs(self):
        store=JobLogStore(Path(self.temp.name)/'logs.sqlite3')
        try:
            for i in range(1010):store.append('one','line '+str(i))
            store.append('other','private other log')
            first=store.read('one');self.assertTrue(first['has_more']);self.assertEqual(len(first['lines']),1000)
            rest=store.read('one',first['cursor'],first['epoch'])
            self.assertEqual(len(rest['lines']),10);self.assertFalse(rest['has_more'])
            self.assertEqual(rest['lines'][-1],'line 1009');self.assertNotIn('private other log',rest['lines'])
        finally:store.close()

    def test_temporary_clear_does_not_touch_persistent_job_logs(self):
        self.job('done');self.store.finish_job('done','failed',0)
        logs=JobLogStore(Path(self.temp.name)/'logs.sqlite3')
        try:
            logs.append('done','giữ nguyên log');self.store.clear_temporary_history()
            self.assertEqual(logs.read('done')['lines'],['giữ nguyên log'])
        finally:logs.close()

    def test_replaying_clear_command_cannot_hide_jobs_completed_after_first_clear(self):
        from core.remote.protocol import MutationJournal
        import uuid
        self.job('old');self.store.finish_job('old','failed',0)
        journal=MutationJournal(Path(self.temp.name)/'operations.sqlite3');request=str(uuid.uuid4())
        first=journal.execute(request,'history.temporary.clear',{},self.store.clear_temporary_history)
        self.job('later');self.store.finish_job('later','completed',1)
        self.assertEqual(journal.execute(request,'history.temporary.clear',{},self.store.clear_temporary_history),first)
        self.assertEqual([r['job_id'] for r in self.store.temporary_history()['rows']],['later'])


if __name__=='__main__':unittest.main(verbosity=2)
