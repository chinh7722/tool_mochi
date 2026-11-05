"""Literal identity searches run before pagination and keep persistent history/logs."""
from pathlib import Path
import sys,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from core.telegram_users import TelegramUserStore
from core.job_search import matches_job


class JobSearchTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=TelegramUserStore(Path(self.temp.name)/'users.sqlite3')
        self.store.upsert_user(123,'Đặng Nguyễn')
        self.store.record_job('bean_42',123,'HELLO_10',1000,10,sale_rate_units=0)
        self.store.record_job('customer-job',-1,'OTHER',100,1,sale_rate_units=0)
        self.store.finish_job('bean_42','failed',2)

    def test_catalog_search_by_code_job_accentless_owner_and_chat_id(self):
        for query in ('hello_10','BEAN_42','dang nguyen','ĐẶNG','123'):
            with self.subTest(query=query):
                self.assertEqual([r['job_id'] for r in self.store.search_jobs(query)['rows']],['bean_42'])

    def test_temporary_search_includes_owner_and_filters_before_pagination(self):
        for i in range(105):
            jid='new'+str(i);self.store.record_job(jid,-1,'OTHER',100,1,sale_rate_units=0);self.store.finish_job(jid,'completed',1)
        result=self.store.temporary_history(query='hello',limit=1)
        self.assertEqual((result['total'],result['rows'][0]['job_id']),(1,'bean_42'))
        self.assertEqual(self.store.temporary_history(query='dang')['total'],1)
        self.assertEqual(self.store.search_jobs('dang',offset=1)['rows'],[])

    def test_sql_wildcards_and_injection_are_literal(self):
        for query in ('%','" OR 1=1 --',"'",'不存在'):
            self.assertEqual(self.store.search_jobs(query)['total'],0)
        self.assertEqual(self.store.search_jobs('_')['total'],1)

    def test_clear_temporary_never_hides_catalog_log_targets(self):
        self.store.clear_temporary_history()
        self.assertEqual(self.store.temporary_history(query='bean')['total'],0)
        self.assertEqual(self.store.search_jobs('bean')['total'],1)

    def test_running_jobs_remain_searchable_but_are_not_temporary(self):
        self.assertEqual(self.store.search_jobs('customer-job')['total'],1)
        self.assertEqual(self.store.temporary_history(query='customer-job')['total'],0)

    def test_validation_and_desktop_identity_match(self):
        for query in (None,123,'x'*161):
            with self.assertRaises(ValueError):self.store.search_jobs(query)
        for args in (dict(offset=True),dict(limit=0),dict(offset=-1)):
            with self.assertRaises(ValueError):self.store.search_jobs(**args)
        row=dict(job_id='abc_12',invite_code='INVITE',creator_name='Đặng',creator_chat_id=456)
        for query in ('ABC_','invite','dang','456'):self.assertTrue(matches_job(row,query))
        self.assertFalse(matches_job(row,'no match'))


if __name__=='__main__':unittest.main()
