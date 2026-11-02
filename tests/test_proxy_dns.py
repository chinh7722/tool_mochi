"""Offline CONNECT_TO pointer and validated DNS-route cache regressions.

Run: py -3.14 -I tests/test_proxy_dns.py
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

import asyncio
import gc
import socket
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from curl_cffi import Curl, CurlOpt, ffi
from curl_cffi.requests.errors import RequestsError

from core.proxy.models import ProxyModel
from core.proxy.dns_routes import (ProxyDnsRoute, resolve_proxy_dns_route,
    get_proxy_dns_route, remember_proxy_dns_route, clear_proxy_dns_route)
import core.proxy.dns_routes as routes


TARGET = 'https://target.example.test/api'
PROXY = 'http://127.0.0.1:8991'


class RouteTests(unittest.TestCase):
    def setUp(self):
        with routes._CACHE_LOCK:
            routes._CACHE.clear()

    def test_exact_host_and_port_rule_is_a_real_curl_slist(self):
        route = ProxyDnsRoute('target.example.test', 8443, '203.0.113.17')
        pointer = route.curl_options()[CurlOpt.CONNECT_TO]
        self.assertEqual(ffi.string(pointer.data), b'target.example.test:8443:203.0.113.17:8443')
        self.assertEqual(pointer.next, ffi.NULL)
        curl = Curl()
        try:
            self.assertEqual(curl.setopt(CurlOpt.CONNECT_TO, pointer), 0)
        finally:
            curl.close()

    def test_session_options_keep_pointer_alive_after_route_is_dropped(self):
        route = ProxyDnsRoute('target.example.test', 443, '203.0.113.18')
        options = route.curl_options()
        del route
        gc.collect()
        self.assertEqual(ffi.string(options[CurlOpt.CONNECT_TO].data),
                         b'target.example.test:443:203.0.113.18:443')

    def test_cache_expires_from_validation_time_and_clear_is_explicit(self):
        route = ProxyDnsRoute('target.example.test', 443, '203.0.113.19', ttl=120)
        with patch(f'{routes.__name__}.time.monotonic', return_value=100):
            remember_proxy_dns_route(PROXY, TARGET, route)
        with patch(f'{routes.__name__}.time.monotonic', return_value=219):
            self.assertIs(get_proxy_dns_route(PROXY, TARGET), route)
        with patch(f'{routes.__name__}.time.monotonic', return_value=220):
            self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))
        remember_proxy_dns_route(PROXY, TARGET, route)
        clear_proxy_dns_route(PROXY, TARGET)
        self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))

    def test_cache_isolated_by_proxy_credentials_target_hostname_and_port(self):
        route = ProxyDnsRoute('target.example.test', 443, '203.0.113.20')
        proxy = ProxyModel.from_string('127.0.0.1:8991:one:secret')
        remember_proxy_dns_route(proxy, TARGET, route)
        self.assertIs(get_proxy_dns_route(proxy.to_playwright_dict(), TARGET), route)
        self.assertIsNone(get_proxy_dns_route('http://one:different@127.0.0.1:8991', TARGET))
        self.assertIsNone(get_proxy_dns_route('http://127.0.0.1:8992', TARGET))
        self.assertIsNone(get_proxy_dns_route(proxy, 'https://other.example.test/api'))
        self.assertIsNone(get_proxy_dns_route(proxy, 'https://target.example.test:8443/api'))
        self.assertIs(get_proxy_dns_route(proxy, 'https://target.example.test/another-path'), route)

    def test_non_https_or_direct_requests_never_get_a_route(self):
        route = ProxyDnsRoute('target.example.test', 443, '203.0.113.21')
        remember_proxy_dns_route(None, TARGET, route)
        remember_proxy_dns_route(PROXY, 'http://target.example.test/', route)
        self.assertIsNone(get_proxy_dns_route(None, TARGET))
        self.assertIsNone(get_proxy_dns_route(PROXY, 'http://target.example.test/'))

    def test_cache_cannot_install_rule_for_another_hostname(self):
        route = ProxyDnsRoute('unrelated.example.test', 443, '203.0.113.22')
        with self.assertRaises(ValueError):
            remember_proxy_dns_route(PROXY, TARGET, route)
        self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))

    def test_wildcards_and_injected_connect_rules_are_rejected(self):
        for host in ('*', 'target.example.test:443:evil:443', 'target.example.test\nother'):
            with self.assertRaises(ValueError):
                ProxyDnsRoute(host, 443, '203.0.113.23')


class ResolverTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with routes._CACHE_LOCK:
            routes._CACHE.clear()

    async def test_resolution_uses_local_ipv4_dns_without_remembering_unvalidated_route(self):
        answer = [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, '', ('203.0.113.24', 443))]
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'getaddrinfo', AsyncMock(return_value=answer)) as resolve:
            route = await resolve_proxy_dns_route(PROXY, TARGET)
        self.assertEqual(route.address, '203.0.113.24')
        self.assertEqual(route.host, 'target.example.test')
        resolve.assert_awaited_once_with('target.example.test', 443,
                                       family=socket.AF_INET, type=socket.SOCK_STREAM)
        self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))
        remember_proxy_dns_route(PROXY, TARGET, route)
        self.assertIs(get_proxy_dns_route(PROXY, TARGET), route)

    async def test_dns_failure_returns_none_without_a_cached_route(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'getaddrinfo', AsyncMock(side_effect=socket.gaierror('offline DNS failure'))):
            self.assertIsNone(await resolve_proxy_dns_route(PROXY, TARGET))
        self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))

    async def test_non_https_or_direct_does_not_resolve(self):
        loop = asyncio.get_running_loop()
        with patch.object(loop, 'getaddrinfo', AsyncMock()) as resolve:
            self.assertIsNone(await resolve_proxy_dns_route(None, TARGET))
            self.assertIsNone(await resolve_proxy_dns_route(PROXY, 'http://target.example.test/'))
        resolve.assert_not_awaited()


class RouteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with routes._CACHE_LOCK:
            routes._CACHE.clear()

    def tcp(self):
        writer = SimpleNamespace(close=Mock(), wait_closed=AsyncMock())
        return AsyncMock(return_value=(SimpleNamespace(), writer))

    def session(self, result=None, *, side_effect=None):
        client = SimpleNamespace(head=AsyncMock(return_value=result, side_effect=side_effect))
        context = AsyncMock()
        context.__aenter__.return_value = client
        return client, context

    async def test_recovery_after_actual_request_failure_skips_duplicate_hostname_probe(self):
        from core.proxy.health import probe_proxy_target
        route = ProxyDnsRoute('target.example.test', 443, '203.0.113.43')
        client, context = self.session(SimpleNamespace(status_code=200))
        with patch('core.proxy.health.asyncio.open_connection', self.tcp()), \
             patch('core.proxy.health.AsyncSession', return_value=context) as factory, \
             patch('core.proxy.health.resolve_proxy_dns_route', AsyncMock(return_value=route)):
            result = await probe_proxy_target(PROXY, target_url=TARGET, timeout=1, recover_dns=True)
        self.assertTrue(result.success)
        self.assertTrue(result.dns_fallback)
        self.assertEqual(factory.call_count, 1)
        client.head.assert_awaited_once_with('https://target.example.test/', allow_redirects=False)
        self.assertIs(factory.call_args.kwargs['curl_options'][CurlOpt.CONNECT_TO], route.curl_options()[CurlOpt.CONNECT_TO])
        self.assertIs(get_proxy_dns_route(PROXY, TARGET), route)

    async def test_failed_recovery_clears_stale_route_and_never_caches_candidate(self):
        from core.proxy.health import probe_proxy_target
        old = ProxyDnsRoute('target.example.test', 443, '203.0.113.44')
        candidate = ProxyDnsRoute('target.example.test', 443, '203.0.113.45')
        remember_proxy_dns_route(PROXY, TARGET, old)
        client, context = self.session(side_effect=RequestsError('offline TLS failure', code=35))
        with patch('core.proxy.health.asyncio.open_connection', self.tcp()), \
             patch('core.proxy.health.AsyncSession', return_value=context), \
             patch('core.proxy.health.resolve_proxy_dns_route', AsyncMock(return_value=candidate)):
            result = await probe_proxy_target(PROXY, target_url=TARGET, timeout=1, recover_dns=True)
        self.assertFalse(result.success)
        self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))

    async def test_failed_hostname_connect_uses_verified_route_then_remembers_it(self):
        from core.proxy.health import probe_proxy_target
        candidate = ProxyDnsRoute('target.example.test', 443, '203.0.113.25')

        async def first_head(*args, **kwargs):
            self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))
            raise RequestsError('TLS connection timed out', code=28)

        async def verified_head(*args, **kwargs):
            self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))
            return SimpleNamespace(status_code=200)

        first, first_context = self.session(side_effect=first_head)
        second, second_context = self.session(side_effect=verified_head)
        with patch('core.proxy.health.asyncio.open_connection', self.tcp()), \
             patch('core.proxy.health.AsyncSession', side_effect=[first_context, second_context]) as factory, \
             patch('core.proxy.health.resolve_proxy_dns_route', AsyncMock(return_value=candidate)):
            result = await probe_proxy_target(PROXY, target_url=TARGET, timeout=1)
        self.assertTrue(result.success)
        self.assertTrue(result.dns_fallback)
        self.assertEqual(result.target_address, candidate.address)
        self.assertIs(get_proxy_dns_route(PROXY, TARGET), candidate)
        self.assertEqual(first.head.call_args.args[0], 'https://target.example.test/')
        self.assertEqual(second.head.call_args.args[0], 'https://target.example.test/')
        self.assertNotIn(CurlOpt.CONNECT_TO, factory.call_args_list[0].kwargs['curl_options'])
        options = factory.call_args_list[1].kwargs
        self.assertIs(options['verify'], True)
        self.assertIs(options['trust_env'], False)
        self.assertEqual(options['proxies'], ProxyModel.from_string(PROXY).to_curl_cffi_dict())
        pointer = options['curl_options'][CurlOpt.CONNECT_TO]
        self.assertEqual(ffi.string(pointer.data), b'target.example.test:443:203.0.113.25:443')

    async def test_failed_fallback_never_populates_validated_cache(self):
        from core.proxy.health import probe_proxy_target
        candidate = ProxyDnsRoute('target.example.test', 443, '203.0.113.26')
        first, first_context = self.session(side_effect=RequestsError('hostname TLS timeout', code=28))
        second, second_context = self.session(side_effect=RequestsError('candidate TLS failure', code=35))
        with patch('core.proxy.health.asyncio.open_connection', self.tcp()), \
             patch('core.proxy.health.AsyncSession', side_effect=[first_context, second_context]), \
             patch('core.proxy.health.resolve_proxy_dns_route', AsyncMock(return_value=candidate)):
            result = await probe_proxy_target(PROXY, target_url=TARGET, timeout=1)
        self.assertFalse(result.success)
        self.assertIsNone(get_proxy_dns_route(PROXY, TARGET))

    async def test_cached_route_is_used_for_initial_https_probe(self):
        from core.proxy.health import probe_proxy_target
        route = ProxyDnsRoute('target.example.test', 443, '203.0.113.27')
        remember_proxy_dns_route(PROXY, TARGET, route)
        client, context = self.session(SimpleNamespace(status_code=200))
        with patch('core.proxy.health.asyncio.open_connection', self.tcp()), \
             patch('core.proxy.health.AsyncSession', return_value=context) as factory, \
             patch('core.proxy.health.resolve_proxy_dns_route', AsyncMock()) as resolve:
            result = await probe_proxy_target(PROXY, target_url=TARGET, timeout=1)
        self.assertTrue(result.success)
        self.assertEqual(factory.call_count, 1)
        resolve.assert_not_awaited()
        self.assertIs(factory.call_args.kwargs['curl_options'][CurlOpt.CONNECT_TO],
                      route.curl_options()[CurlOpt.CONNECT_TO])

    async def test_fetcher_uses_validated_route_only_in_compatibility_retry(self):
        from core.platforms.mochichat.constants import MOCHI_API_BASE
        from core.platforms.mochichat.fetcher import MochiChatFetcher
        from urllib.parse import urlsplit
        host = urlsplit(MOCHI_API_BASE).hostname
        route = ProxyDnsRoute(host, 443, '203.0.113.28')
        remember_proxy_dns_route(PROXY, MOCHI_API_BASE, route)
        session = SimpleNamespace(close=AsyncMock())
        with patch('core.platforms.mochichat.fetcher.AsyncSession', return_value=session) as factory:
            fetcher = MochiChatFetcher(proxy=ProxyModel.from_string(PROXY))
            self.assertNotIn('curl_options', factory.call_args.kwargs)
            fetcher._http1_compatibility = True
            await fetcher._reset_session()
        options = factory.call_args.kwargs
        self.assertIs(options['verify'], True)
        self.assertIs(options['trust_env'], False)
        self.assertEqual(options['proxies'], ProxyModel.from_string(PROXY).to_curl_cffi_dict())
        self.assertIs(options['curl_options'][CurlOpt.CONNECT_TO], route.curl_options()[CurlOpt.CONNECT_TO])
        self.assertEqual(fetcher.api_base, MOCHI_API_BASE)
        await fetcher.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
