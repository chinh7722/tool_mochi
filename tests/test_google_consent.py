"""Google consent regressions using Chrome and intercepted requests only.

Run: py -3.14 -I tests/test_google_consent.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import asyncio
import unittest
from urllib.parse import urlsplit
from unittest.mock import AsyncMock, patch

from playwright.async_api import Locator, TimeoutError as PlaywrightTimeoutError

from core.auth import google
from core.browser.manager import CentralBrowserManager


class GoogleConsentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.manager = CentralBrowserManager()
        self.addAsyncCleanup(self.manager.shutdown_for_loop)
        self.submissions = []
        self.rpc_count = 0

    async def run_flow(self, body, script, *, rpc_delay=0, native_click=None):
        context = await self.manager.acquire_context(self.id(), headless=True)
        await context.expose_function('recordSubmission', lambda value: self.submissions.append(value))
        document = '''<!doctype html><html><body>
            <main><h1>Sign in to lightchaser.xyz</h1>
            <p>Google will allow lightchaser.xyz to access this info about you</p>
            ''' + body + '''</main><script>
            window.ready = true;
            const callback = 'https://mochi.lightchaser.xyz/#id_token=eyJconsent-test';
            ''' + script + '</script></body></html>'

        async def serve(route):
            if urlsplit(route.request.url).path == '/consent-rpc':
                self.rpc_count += 1
                if rpc_delay:
                    await asyncio.sleep(rpc_delay)
                try:
                    await route.fulfill(status=200, content_type='application/json', body='{}')
                except Exception:
                    # A failing old implementation may issue extra requests and
                    # close the context as the first request finishes.
                    if not context.pages:
                        return
                    raise
            else:
                await route.fulfill(status=200, content_type='text/html', body=document)

        await context.route('**/*', serve)
        with patch.object(google.browser_manager, 'acquire_context', AsyncMock(return_value=context)), \
             patch.object(google.browser_manager, 'release_context', self.manager.release_context):
            if native_click is None:
                return await google._extract_id_token_via_browser(self.id(), 'test@example.com',
                    'offline-password', 'offline-client', timeout=4, headless=True, max_retries=1)
            with patch.object(Locator, 'click', native_click):
                return await google._extract_id_token_via_browser(self.id(), 'test@example.com',
                    'offline-password', 'offline-client', timeout=4, headless=True, max_retries=1)

    async def test_hidden_stale_marker_does_not_hide_current_continue(self):
        token = await self.run_flow('''
            <button id="old" data-ttm-login-action="consent" style="display:none">Continue</button>
            <button id="current"><span>Continue</span></button>
        ''', '''
            document.querySelector('#old').onclick = () => window.recordSubmission('hidden');
            document.querySelector('#current').onclick = async () => {
                await window.recordSubmission('current');
                location.href = callback;
            };
        ''')
        self.assertEqual(token, 'eyJconsent-test')
        self.assertEqual(self.submissions, ['current'])

    async def test_slow_consent_rpc_is_submitted_once(self):
        token = await self.run_flow('<button id="continue">Continue</button>', '''
            document.querySelector('#continue').onclick = async () => {
                await window.recordSubmission('continue');
                await fetch('/consent-rpc', {method: 'POST'});
                location.href = callback;
            };
        ''', rpc_delay=1.9)
        self.assertEqual(token, 'eyJconsent-test')
        self.assertEqual(self.submissions, ['continue'])
        self.assertEqual(self.rpc_count, 1)

    async def test_busy_consent_waits_for_page_readiness(self):
        token = await self.run_flow('''
            <div id="progress" role="progressbar">Loading</div>
            <button id="continue">Continue</button>
        ''', '''
            window.ready = false;
            document.body.setAttribute('aria-busy', 'true');
            setTimeout(() => {
                window.ready = true;
                document.body.removeAttribute('aria-busy');
                document.querySelector('#progress').remove();
            }, 650);
            document.querySelector('#continue').onclick = async () => {
                await window.recordSubmission(window.ready);
                location.href = callback;
            };
        ''')
        self.assertEqual(token, 'eyJconsent-test')
        self.assertEqual(self.submissions, [True])

    async def test_visible_progress_alone_prevents_early_continue(self):
        token = await self.run_flow('''
            <div id="progress" role="progressbar">Loading</div>
            <button id="continue">Continue</button>
        ''', '''
            window.ready = false;
            setTimeout(() => {
                window.ready = true;
                document.querySelector('#progress').remove();
            }, 550);
            document.querySelector('#continue').onclick = async () => {
                await window.recordSubmission(window.ready);
                location.href = callback;
            };
        ''')
        self.assertEqual(token, 'eyJconsent-test')
        self.assertEqual(self.submissions, [True])

    async def test_click_delivery_error_does_not_duplicate_fallback_submit(self):
        original_click = Locator.click
        raised_once = False

        async def delivered_then_error(locator, *args, **kwargs):
            nonlocal raised_once
            result = await original_click(locator, *args, **kwargs)
            if not raised_once:
                raised_once = True
                # The browser received the click, but Playwright failed while
                # completing the action. Resending events would duplicate it.
                raise PlaywrightTimeoutError('Action completed; response still loading')
            return result

        token = await self.run_flow('<button id="continue">Continue</button>', '''
            document.querySelector('#continue').onclick = async () => {
                await window.recordSubmission('continue');
                await fetch('/consent-rpc', {method: 'POST'});
                location.href = callback;
            };
        ''', rpc_delay=0.7, native_click=delivered_then_error)
        self.assertEqual(token, 'eyJconsent-test')
        self.assertTrue(raised_once)
        self.assertEqual(self.submissions, ['continue'])
        self.assertEqual(self.rpc_count, 1)

    async def test_nested_continue_with_descriptive_aria_label_avoids_cancel(self):
        token = await self.run_flow('''
            <button id="cancel" aria-label="Cancel">Cancel</button>
            <button id="continue" aria-label="Continue to lightchaser.xyz">
                <span><span>Continue</span></span>
            </button>
        ''', '''
            document.querySelector('#cancel').onclick = () => window.recordSubmission('cancel');
            document.querySelector('#continue').onclick = async () => {
                await window.recordSubmission('continue');
                location.href = callback;
            };
        ''')
        self.assertEqual(token, 'eyJconsent-test')
        self.assertEqual(self.submissions, ['continue'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
