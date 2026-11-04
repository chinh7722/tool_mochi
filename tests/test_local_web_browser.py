"""Real Chromium checks for the localhost UI with a fully offline VPS substitute."""
import asyncio
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'tests'))
from _local_web_fixtures import WebFixture,TOKEN,user
from playwright.async_api import async_playwright


class LocalWebBrowserTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture=WebFixture()
        self.addCleanup(self.fixture.close)
        self.pw=await async_playwright().start()
        self.addAsyncCleanup(self.pw.stop)
        system_chrome=next((path for path in (Path('C:/Program Files/Google/Chrome/Application/chrome.exe'),
            Path('C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe')) if path.is_file()),None)
        self.browser=await self.pw.chromium.launch(headless=True,
            **({'executable_path':str(system_chrome)} if system_chrome else {}))
        self.addAsyncCleanup(self.browser.close)
        self.context=await self.browser.new_context(viewport={'width':1280,'height':900},accept_downloads=True)
        self.page=await self.context.new_page()
        self.errors=[]
        self.urls=[]
        self.page.on('pageerror',lambda error:self.errors.append(str(error)))
        self.page.on('request',lambda request:self.urls.append(request.url))
        await self.page.goto(self.fixture.server.browser_url)
        await self.page.locator('#sidebar-connection.online').wait_for()

    async def idle(self,action=None):
        await self.page.locator('#job-start:not([disabled])').wait_for(state='attached')

    async def sent(self,action,count=1):
        for _ in range(100):
            if len([r for r in self.fixture.fake.commands if r[0]==action])>=count:
                await self.idle()
                return [r[1] for r in self.fixture.fake.commands if r[0]==action][-1]
            await asyncio.sleep(.04)
        self.fail('No offline command: '+action+'; JS errors: '+str(self.errors))

    async def nav(self,name):
        await self.page.locator('nav [data-page="'+name+'"]').click()

    async def test_boot_and_all_six_pages_work_without_external_requests_or_exposed_credentials(self):
        for name in ('dashboard','mochi','proxy','mailproxy','telegram','settings'):
            await self.nav(name)
            self.assertTrue(await self.page.locator('[data-view="'+name+'"]').is_visible())
        self.assertEqual(self.errors,[])
        self.assertTrue(all(url.startswith(self.fixture.base) for url in self.urls))
        self.assertNotIn(TOKEN,await self.page.content())
        self.assertNotIn('#session=',self.page.url)

    async def test_create_scheduled_combo_targets_full_amount_and_focuses_new_job(self):
        f=self.page.locator('#job-form')
        await f.locator('[name="code"]').fill('COMBO1')
        await f.locator('[name="customer"]').fill('Khách Combo')
        await f.locator('[name="points"]').fill('5500')
        await f.locator('[name="combo"]').check()
        await f.locator('[name="scheduled"]').check()
        await f.locator('[name="scheduled_at"]').fill('2099-11-04T09:30')
        await self.page.locator('#job-start').click()
        payload=await self.sent('job.create')
        self.assertEqual((payload['code'],payload['points'],payload['combo']),('COMBO1',5500,True))
        self.assertEqual(payload['scheduled_at'],'2099-11-04T09:30:00+07:00')
        self.assertTrue(await self.page.locator('[data-panel="jobs:running"]').is_visible())
        self.assertEqual(await self.page.locator('#jobs-table tbody tr').first.get_attribute('data-key'),'web-job-1')

    async def test_polling_preserves_job_draft_and_logs_are_plain_text_for_correct_id(self):
        await self.page.locator('#job-form [name="customer"]').fill('Chưa gửi')
        await self.page.locator('.topbar [data-action="refresh"]').click()
        await self.idle()
        self.assertEqual(await self.page.locator('#job-form [name="customer"]').input_value(),'Chưa gửi')
        await self.page.locator('[data-tabs="jobs"] [data-tab="running"]').click()
        await self.page.locator('#jobs-table [data-action="job-logs"]').click()
        payload=await self.sent('job.logs')
        self.assertEqual(payload['job_id'],'old-job')
        self.assertIn('<log is plain text>',await self.page.locator('#job-log').inner_text())
        self.assertEqual(await self.page.locator('#job-log log').count(),0)

    async def test_broadcast_selects_only_checked_person_and_never_sends_empty(self):
        await self.nav('telegram')
        await self.page.locator('#broadcast-mode').select_option('custom')
        await self.page.locator('#broadcast-text').fill('Thông báo riêng\nGiữ xuống dòng.')
        self.assertTrue(await self.page.locator('#broadcast-send').is_disabled())
        await self.page.locator('[data-recipient="202"]').check()
        await self.page.locator('#users-search').fill('Admin')
        await self.page.locator('#broadcast-send').click()
        payload=await self.sent('telegram.broadcast')
        self.assertEqual(payload,{'text':'Thông báo riêng\nGiữ xuống dòng.','recipients':[202]})
        self.assertTrue(await self.page.locator('#broadcast-send').is_disabled())
        self.assertTrue(await self.page.locator('#broadcast-cancel').is_enabled())

    async def test_custom_selection_survives_polling_and_new_member_stays_unchecked(self):
        await self.nav('telegram')
        await self.page.locator('#broadcast-mode').select_option('custom')
        await self.page.locator('[data-recipient="202"]').check()
        self.fixture.fake.value['users'].append(user(606,'Người mới'))
        self.fixture.fake.value['users'][2]['name']='Partner đã đổi tên'
        await self.page.locator('.topbar [data-action="refresh"]').click()
        await self.page.wait_for_selector('[data-recipient="606"]')
        self.assertTrue(await self.page.locator('[data-recipient="202"]').is_checked())
        self.assertFalse(await self.page.locator('[data-recipient="606"]').is_checked())

    async def test_old_vps_capability_keeps_announcement_disabled(self):
        self.fixture.fake.value['telegram'].pop('broadcast_recipient_selection')
        await self.page.locator('.topbar [data-action="refresh"]').click()
        await self.nav('telegram')
        await self.page.locator('#broadcast-text').fill('Test')
        await self.page.locator('#broadcast-audience').filter(has_text='hỗ trợ').wait_for()
        self.assertTrue(await self.page.locator('#broadcast-send').is_disabled())
        self.assertFalse(self.fixture.fake.commands)

    async def test_partner_form_and_large_chat_id_forward_without_rounding(self):
        await self.nav('telegram')
        f=self.page.locator('#user-form')
        await f.locator('[name="chat_id"]').fill('9223372036854775806')
        await f.locator('[name="name"]').fill('Partner mới')
        await f.locator('button[type="submit"]').click()
        payload=await self.sent('user.save')
        self.assertEqual(payload['chat_id'],9223372036854775806)
        self.assertEqual(payload['name'],'Partner mới')

    async def test_selected_user_private_keys_and_user_jobs_use_same_owner(self):
        await self.nav('telegram')
        await self.page.locator('#users-table tr[data-key="202"]').click()
        f=self.page.locator('#user-keys-form')
        await f.locator('[name="gmailvip_key"]').fill('offline-gmail-key')
        await f.locator('[name="one_trieu_key"]').fill('offline-trieu-key')
        await f.locator('button[type="submit"]').click()
        payload=await self.sent('user.mail_keys')
        self.assertEqual(payload['chat_id'],202)
        self.assertEqual(await f.locator('[name="gmailvip_key"]').input_value(),'')
        await self.page.locator('[data-action="user-jobs"]').click()
        await self.sent('user.jobs')
        self.assertTrue(await self.page.locator('#dialog').is_visible())
        self.assertIn('owned-job',await self.page.locator('#dialog-body').inner_text())

    async def test_balance_dialog_preserves_expected_balance_and_customer_identity(self):
        await self.nav('telegram')
        await self.page.locator('[data-tabs="telegram"] [data-tab="customers"]').click()
        await self.page.locator('#customers-table tr[data-key="505"]').click()
        await self.page.locator('[data-action="customer-balance"]').click()
        await self.page.locator('#balance-form [name="amount"]').fill('2000.25')
        await self.page.locator('#balance-form [name="reason"]').fill('Điều chỉnh theo yêu cầu')
        await self.page.locator('#balance-form button[type="submit"]').click()
        payload=await self.sent('customer.balance')
        self.assertEqual(payload,dict(chat_id=505,expected_units=123450,amount='2000.25',reason='Điều chỉnh theo yêu cầu'))

    async def test_proxy_import_pool_and_provider_are_explicit_and_export_downloads(self):
        await self.nav('mailproxy')
        await self.page.locator('#proxy-provider-mail').select_option('muaproxyxoay')
        await self.page.locator('[data-action="proxy-paste"][data-pool="mail"]').click()
        await self.page.locator('#proxy-paste-form textarea').fill('127.0.0.1:8080:u:p|rotation-key')
        await self.page.locator('#proxy-paste-form button[type="submit"]').click()
        payload=await self.sent('proxy.import')
        self.assertEqual((payload['pool'],payload['provider']),('mail','muaproxyxoay'))
        async with self.page.expect_download() as info:
            await self.page.locator('[data-action="proxy-export"][data-pool="mail"]').click()
        self.assertEqual((await info.value).suggested_filename,'proxy_mua_mail.txt')

    async def test_settings_polling_does_not_overwrite_edits_and_blank_keys_preserve_server_values(self):
        await self.nav('settings')
        await self.page.locator('[data-tabs="settings"] [data-tab="mochi"]').click()
        f=self.page.locator('#settings-form')
        await f.locator('[name="job_limit"]').fill('7')
        await self.page.locator('.topbar [data-action="refresh"]').click()
        await self.idle()
        self.assertEqual(await f.locator('[name="job_limit"]').input_value(),'7')
        await f.locator('button[type="submit"]').click()
        payload=await self.sent('settings.save')
        self.assertEqual(payload['job_limit'],7)
        self.assertEqual(payload['gmailvip_key'],'')
        self.assertIs(payload['mail_proxy'],True)

    async def test_lookup_password_is_cleared_and_closing_browser_does_not_stop_vps(self):
        await self.page.locator('[data-tabs="mochi-workspace"] [data-tab="lookup"]').click()
        f=self.page.locator('#lookup-form')
        await f.locator('[name="email"]').fill('offline@example.test')
        await f.locator('[name="password"]').fill('offline-password')
        await f.locator('button[type="submit"]').click()
        payload=await self.sent('lookup.start')
        self.assertEqual(payload['password'],'offline-password')
        self.assertEqual(await f.locator('[name="password"]').input_value(),'')
        await self.page.close()
        self.assertFalse(any(action.endswith('.stop') for action,_,_ in self.fixture.fake.commands))

    async def test_untrusted_values_render_as_text_and_layout_fits_small_desktop(self):
        self.fixture.fake.value['users'][2]['name']='<img src=x onerror="window.injected=true">'
        await self.page.locator('.topbar [data-action="refresh"]').click()
        await self.nav('telegram')
        await self.page.locator('#users-table').filter(has_text='onerror').wait_for()
        self.assertIsNone(await self.page.evaluate('window.injected'))
        await self.page.set_viewport_size({'width':1120,'height':760})
        self.assertTrue(await self.page.evaluate('document.documentElement.scrollWidth<=innerWidth'))
        self.assertEqual(self.errors,[])

    async def test_inventory_lists_created_accounts_and_hides_raw_mail_receipts(self):
        await self.page.locator('[data-tabs="mochi-workspace"] [data-tab="inventory"]').click()
        await self.sent('stock.list')
        await self.page.locator('#stock-table').filter(has_text='stock1@example.test').wait_for()
        self.assertIn('3000',await self.page.locator('#stock-summary').inner_text())
        self.assertFalse(await self.page.locator('#job-batch').is_visible())
        self.assertFalse(any(a=='batch.list' for a,_,_ in self.fixture.fake.commands))
        self.assertEqual(self.errors,[])

    async def test_create_stock_form_sends_account_count_product_and_infinite_daily_mode(self):
        await self.page.locator('[data-stock-type="stock"]').click()
        form=self.page.locator('#stock-form')
        await form.locator('[name="count"]').fill('5')
        await form.locator('[name="beans"]').fill('5500')
        await form.locator('[name="product_ids"]').fill('123')
        await form.locator('[name="gmailvip_key"]').fill('creation-gmail')
        await form.locator('[name="one_trieu_key"]').fill('creation-trieu')
        await form.locator('[name="infinite"]').check()
        self.assertTrue(await form.locator('[name="beans"]').is_disabled())
        self.assertFalse(await self.page.locator('#job-form').is_visible())
        await self.page.set_viewport_size({'width':1120,'height':760})
        await self.page.screenshot(path=str(ROOT/'.tmp/stock_creation_web.png'),full_page=True)
        await form.locator('button[type="submit"]').click()
        payload=await self.sent('stock.create')
        self.assertEqual(payload,dict(count=5,beans=5500,infinite=True,product_ids='123',
            gmailvip_key='creation-gmail',one_trieu_key='creation-trieu'))
        self.assertEqual(await form.locator('[name="gmailvip_key"]').input_value(),'')
        self.assertEqual(await form.locator('[name="one_trieu_key"]').input_value(),'')
        self.assertFalse(any(a=='job.create' for a,_,_ in self.fixture.fake.commands))
        self.assertEqual(self.errors,[])

    async def test_stock_delete_cancel_keeps_acc_then_confirm_deletes_only_selected(self):
        await self.page.locator('[data-tabs="mochi-workspace"] [data-tab="inventory"]').click()
        await self.page.locator('[data-stock-check="1"]').check()
        self.page.once('dialog',lambda d:d.dismiss())
        await self.page.locator('#stock-delete').click()
        self.assertFalse(any(a=='stock.delete' for a,_,_ in self.fixture.fake.commands))
        self.page.once('dialog',lambda d:d.accept())
        await self.page.locator('#stock-delete').click()
        self.assertEqual((await self.sent('stock.delete'))['ids'],[1])
        await self.page.locator('[data-stock-check="1"]').wait_for(state='detached')
        await self.page.locator('#stock-table').filter(has_text='stock2@example.test').wait_for()
        self.assertNotIn('stock1@example.test',await self.page.locator('#stock-table').inner_text())

    async def test_stock_export_preview_keeps_acc_until_close_and_history_can_reopen(self):
        await self.page.locator('[data-tabs="mochi-workspace"] [data-tab="inventory"]').click()
        await self.page.locator('[data-stock-check="1"]').check()
        await self.page.locator('#stock-export').click()
        await self.page.locator('#stock-export-dialog[open]').wait_for()
        self.assertEqual(await self.page.locator('#stock-export-content').input_value(),'stock1@example.test|offline-pw1')
        self.assertFalse(any(a=='stock.export.finish' for a,_,_ in self.fixture.fake.commands))
        await self.page.locator('#stock-export-close').click()
        await self.sent('stock.export.finish')
        await self.page.locator('#stock-exports [data-stock-show]').wait_for()
        self.assertNotIn('stock1@example.test',await self.page.locator('#stock-table').inner_text())
        await self.page.locator('#stock-exports [data-stock-show]').click()
        await self.page.locator('#stock-export-dialog[open]').wait_for()
        self.assertEqual(await self.page.locator('#stock-export-content').input_value(),'stock1@example.test|offline-pw1')
        await self.page.locator('#stock-export-close').click()
        self.assertEqual(self.errors,[])

    async def test_stock_selection_survives_refresh_and_passwords_render_as_plain_text(self):
        self.fixture.fake.stock_rows[0]['password']='<img src=x onerror="window.injected=true">'
        await self.page.locator('[data-tabs="mochi-workspace"] [data-tab="inventory"]').click()
        await self.page.locator('[data-stock-check="1"]').check()
        await self.page.locator('#stock-refresh').click()
        self.assertTrue(await self.page.locator('[data-stock-check="1"]').is_checked())
        self.assertIsNone(await self.page.evaluate('window.injected'))
        self.assertEqual(await self.page.locator('#stock-table img').count(),0)
        await self.page.set_viewport_size({'width':1120,'height':760})
        await self.page.screenshot(path=str(ROOT/'.tmp/stock_inventory_web.png'),full_page=True)

    async def test_bot_and_sepay_settings_clear_entered_secrets_after_save(self):
        await self.nav('telegram')
        await self.page.locator('#bot-form [name="token"]').fill('offline-new-bot-token')
        await self.page.locator('#bot-form button[type="submit"]').click()
        self.assertEqual((await self.sent('telegram.save'))['token'],'offline-new-bot-token')
        self.assertEqual(await self.page.locator('#bot-form [name="token"]').input_value(),'')
        await self.page.locator('[data-tabs="telegram"] [data-tab="sepay"]').click()
        f=self.page.locator('#sepay-form')
        await f.locator('[name="account_number"]').fill('12345678')
        await f.locator('[name="bank_name"]').fill('Ngân hàng kiểm thử')
        await f.locator('[name="token"]').fill('offline-sepay-key')
        await f.locator('[name="enabled"]').check()
        await f.locator('button[type="submit"]').click()
        payload=await self.sent('sepay.save')
        self.assertEqual(payload['account_number'],'12345678')
        self.assertIs(payload['enabled'],True)
        self.assertEqual(await f.locator('[name="token"]').input_value(),'')

    async def test_offline_state_disables_mutations_but_connection_and_pages_stay_available(self):
        self.fixture.fake.offline=True
        await self.page.locator('.topbar [data-action="refresh"]').click()
        await self.page.locator('#job-start[disabled]').wait_for(state='attached')
        await self.nav('settings')
        self.assertTrue(await self.page.locator('#connect-button').is_enabled())
        self.assertNotIn(TOKEN,await self.page.content())
        self.assertFalse(self.fixture.fake.commands)

    async def test_hidden_user_selection_cannot_receive_partner_key_changes(self):
        await self.nav('telegram')
        await self.page.locator('#users-table tr[data-key="202"]').click()
        await self.page.locator('#users-search').fill('Admin')
        f=self.page.locator('#user-keys-form')
        await f.locator('[name="gmailvip_key"]').fill('fake-key')
        await f.locator('[name="one_trieu_key"]').fill('fake-key-two')
        await f.locator('button[type="submit"]').click()
        await self.page.locator('#toast').filter(has_text='Chọn một dòng').wait_for()
        self.assertFalse(any(action=='user.mail_keys' for action,_,_ in self.fixture.fake.commands))

    async def test_user_reports_download_two_csv_files_for_selected_owner(self):
        await self.nav('telegram')
        await self.page.locator('#users-table tr[data-key="202"]').click()
        await self.page.locator('#user-form [name="chat_id"]').fill('303')
        await self.page.locator('[data-action="user-report"]').click()
        payload=await self.sent('user.report')
        self.assertEqual(payload,{'chat_id':202,'kind':'all'})
        self.assertIn('Partner A',await self.page.locator('#dialog-body').inner_text())
        for index,kind in enumerate(('jobs','purchases')):
            async with self.page.expect_download() as info:
                await self.page.locator(f'[data-action="report-download"][data-index="{index}"]').click()
            download=await info.value
            self.assertEqual(download.suggested_filename,f'hoshino_202_{kind}.csv')
            content=Path(await download.path()).read_bytes()
            self.assertTrue(content.startswith(b'\xef\xbb\xbf'))
            self.assertEqual(content.count(b'\xef\xbb\xbf'),1)
            self.assertIn(b'202,offline-',content)

    async def test_user_reports_require_visible_selection_and_allow_local(self):
        await self.nav('telegram')
        await self.page.locator('[data-action="user-report"]').click()
        await self.page.locator('#toast').filter(has_text='Chọn một dòng').wait_for()
        self.assertFalse(self.fixture.fake.commands)
        await self.page.locator('#users-table tr[data-key="202"]').click()
        await self.page.locator('#users-search').fill('Admin')
        await self.page.locator('[data-action="user-report"]').click()
        self.assertFalse(self.fixture.fake.commands)
        await self.page.locator('#users-search').fill('')
        await self.page.locator('#users-table tr[data-key="-1"]').click()
        await self.page.locator('[data-action="user-report"]').click()
        self.assertEqual((await self.sent('user.report'))['chat_id'],-1)

    async def test_report_owner_mismatch_is_rejected_before_download(self):
        self.fixture.fake.report_override=dict(owner={'chat_id':303,'name':'Other partner'},
            files=[{'kind':'jobs','filename':'other.csv','text':'fake','row_count':1},
                   {'kind':'purchases','filename':'mail.csv','text':'fake','row_count':1}],generated_at='')
        await self.nav('telegram')
        await self.page.locator('#users-table tr[data-key="202"]').click()
        await self.page.locator('[data-action="user-report"]').click()
        await self.sent('user.report')
        await self.page.locator('#toast').filter(has_text='không đúng người').wait_for()
        self.assertFalse(await self.page.locator('#dialog').is_visible())


    async def test_stop_confirmation_can_cancel_or_confirm_without_affecting_telegram(self):
        self.fixture.fake.value['jobs'][0]['status']='Đang chạy'
        await self.page.locator('.topbar [data-action="refresh"]').click()
        await self.idle()
        await self.page.locator('[data-tabs="jobs"] [data-tab="running"]').click()
        async def dismiss(dialog):await dialog.dismiss()
        self.page.once('dialog',dismiss)
        await self.page.locator('#jobs-table [data-action="job-stop"]').first.click()
        self.assertFalse(any(c[0]=='job.stop' for c in self.fixture.fake.commands))
        async def accept(dialog):await dialog.accept()
        self.page.once('dialog',accept)
        await self.page.locator('#jobs-table [data-action="job-stop"]').first.click()
        payload=await self.sent('job.stop')
        self.assertTrue(payload['job_id'])

    async def test_live_log_appends_new_lines_without_reopening_or_duplicate_old_lines(self):
        await self.page.locator('[data-tabs="jobs"] [data-tab="running"]').click()
        await self.page.locator('#jobs-table [data-action="job-logs"]').first.click()
        await self.sent('job.logs')
        self.fixture.fake.log_lines.append('Dòng mới theo thời gian thực')
        await self.page.locator('#job-log').filter(has_text='Dòng mới theo thời gian thực').wait_for()
        text=await self.page.locator('#job-log').text_content()
        self.assertEqual(text.count('<log is plain text>'),1)
        self.assertEqual(text.count('Dòng mới theo thời gian thực'),1)
        self.assertEqual(self.errors,[])

    async def test_statistics_charts_drilldown_owner_filter_and_history_location(self):
        await self.nav('statistics')
        await self.sent('statistics.get')
        await self.page.locator('#stats-profit [data-stats-day]').first.wait_for()
        self.assertEqual(await self.page.locator('[data-tabs="jobs"] [data-tab="history"]').count(),0)
        await self.page.locator('#stats-profit [data-stats-day]').nth(3).click()
        payload=await self.sent('statistics.get',2)
        self.assertTrue(payload['day'].endswith('-04'))
        await self.page.locator('#history-table').filter(has_text='TỔNG Partner A').wait_for()
        await self.page.locator('#stats-owner').select_option('202')
        payload=await self.sent('statistics.get',3)
        self.assertEqual(str(payload['owner_id']),'202')
        self.assertNotIn('Partner B',await self.page.locator('#history-table').text_content())
        await self.page.locator('#stats-customers [data-stats-day]').nth(3).click()
        await self.sent('statistics.get',4)
        await self.page.locator('#stats-detail-caption').filter(has_text='lượt khách').wait_for()
        self.assertIn('CODE202',await self.page.locator('#history-table').text_content())
        self.assertIn('hôm nay 2',await self.page.locator('#stats-customers-summary').text_content())
        self.assertIn('100.0%',await self.page.locator('#stats-reasons').text_content())
        self.assertEqual(self.errors,[])
        await self.page.screenshot(path=str(ROOT/'.tmp/statistics_web.png'),full_page=True)


    async def test_price_milestone_save_and_ratio_quote_on_job_form(self):
        await self.nav('prices')
        await self.page.locator('#price-beans').fill('2000')
        await self.page.locator('#price-vnd').fill('15000')
        await self.page.locator('#price-form button[type="submit"]').click()
        await self.page.locator('#prices-save').click()
        payload=await self.sent('pricing.save')
        self.assertEqual(payload['prices'],[dict(beans=2000,price_units=1500000)])
        await self.nav('mochi')
        await self.page.locator('#job-form [name="points"]').fill('1000')
        self.assertEqual(await self.page.locator('#job-quote').input_value(),'7.500')
        self.assertEqual(self.errors,[])

    async def test_temporary_history_clear_confirmation_keeps_permanent_statistics_and_new_jobs_appear(self):
        permanent=self.fixture.fake.value['history'].copy()
        await self.page.locator('[data-tabs="jobs"] [data-tab="temporary"]').click()
        await self.sent('history.temporary')
        await self.page.locator('#temporary-table').filter(has_text='temp-failed').wait_for()
        async def dismiss(dialog):await dialog.dismiss()
        self.page.once('dialog',dismiss);await self.page.locator('#temporary-clear').click()
        self.assertFalse(any(c[0]=='history.temporary.clear' for c in self.fixture.fake.commands))
        async def accept(dialog):await dialog.accept()
        self.page.once('dialog',accept);await self.page.locator('#temporary-clear').click()
        await self.sent('history.temporary.clear')
        await self.page.locator('#temporary-table .empty').wait_for()
        self.assertEqual(self.fixture.fake.value['history'],permanent)
        self.fixture.fake.temporary_rows=[dict(job_id='after-clear',code='NEW',name='New user',chat_id=202,status='completed',target_count=1,success_count=1,can_continue=False,finalized_at='2026-11-04T10:00:00+07:00')]
        await self.page.locator('#temporary-refresh').click()
        await self.page.locator('#temporary-table').filter(has_text='after-clear').wait_for()
        self.assertEqual(self.errors,[])

    async def test_unique_customer_chart_and_explicit_all_days_selector(self):
        await self.nav('statistics');await self.sent('statistics.get')
        await self.page.locator('#stats-unique-customers [data-stats-day]').nth(3).click()
        await self.sent('statistics.get',2)
        await self.page.locator('#stats-detail-caption').filter(has_text='mã mời duy nhất').wait_for()
        self.assertIn('ONECODE',await self.page.locator('#history-table').text_content())
        self.assertEqual(await self.page.locator('#stats-day').input_value(),(await self.page.locator('#stats-month').input_value())+'-04')
        await self.page.locator('#stats-day').select_option('')
        payload=await self.sent('statistics.get',3)
        self.assertNotIn('day',payload)
        await self.page.locator('#stats-detail-caption').filter(has_text='Lịch sử job').wait_for()
        self.assertEqual(self.errors,[])
        await self.page.screenshot(path=str(ROOT/'.tmp/unique_customers_web.png'),full_page=True)

    async def test_temporary_failed_job_can_continue_and_view_its_log(self):
        await self.page.locator('[data-tabs="jobs"] [data-tab="temporary"]').click();await self.sent('history.temporary')
        await self.page.locator('#temporary-table [data-action="job-logs"]').click()
        payload=await self.sent('job.logs');self.assertEqual(payload['job_id'],'temp-failed')
        await self.page.locator('[data-tabs="jobs"] [data-tab="temporary"]').click()
        await self.page.locator('#temporary-table [data-action="job-continue"]').click()
        payload=await self.sent('job.continue');self.assertEqual(payload['job_id'],'temp-failed')


if __name__=='__main__': unittest.main()
