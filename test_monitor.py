import unittest
import os
import urllib.request
from unittest.mock import patch
import monitor


class MonitorTests(unittest.TestCase):
    def test_308_canonical_redirect_works_on_older_python(self):
        request = urllib.request.Request('https://doura.tn/')
        redirected = monitor.SafeRedirects().redirect_request(request, None, 308, '', {}, monitor.ORIGIN + '/')
        self.assertEqual(redirected.full_url, monitor.ORIGIN + '/')

    def test_redirects_cannot_forward_headers_to_another_host(self):
        request = urllib.request.Request(monitor.API + '/auth/v1/settings')
        with self.assertRaises(ValueError):
            monitor.SafeRedirects().redirect_request(request, None, 302, '', {}, 'https://other.invalid/')

    def test_html_fallback_is_not_a_healthy_app(self):
        with patch.object(monitor, 'fetch', return_value=(200, monitor.ORIGIN, 'text/html', b'<h1>Unavailable</h1>')):
            with self.assertRaises(ValueError):
                monitor.html_check(monitor.ORIGIN)

    def test_app_shell_requires_compiled_assets(self):
        with patch.object(monitor, 'fetch', return_value=(200, monitor.ORIGIN, 'text/html', b'<div id="root"></div>')):
            with self.assertRaises(ValueError):
                monitor.html_check(monitor.ORIGIN)

    def test_public_bootstrap_is_scoped_to_expected_api(self):
        page = monitor.Page('<script src="/home-hero-bootstrap.js" data-origin="https://other.invalid" data-key="not-used"></script>')
        self.assertIsNone(page.key)

    def test_confirmed_failure_opens_once_and_recovery_closes(self):
        bad, good = [{'ok': False}], [{'ok': True}]
        self.assertEqual(monitor.incident_action(bad, None), 'open')
        self.assertEqual(monitor.incident_action(bad, {'number': 1}), 'none')
        self.assertEqual(monitor.incident_action(good, {'number': 1}), 'recover')
        self.assertEqual(monitor.incident_action(good, None), 'none')

    def test_transient_failure_is_confirmed_before_alerting(self):
        with patch.object(monitor.time, 'sleep'), patch('monitor.html_check', side_effect=[OSError(), None]) as probe:
            self.assertTrue(monitor.run_check('example', lambda: probe())['ok'])
            self.assertEqual(probe.call_count, 2)

    def test_delivery_test_recovers_even_when_issue_list_is_stale(self):
        with patch.dict(os.environ, {'GITHUB_REPOSITORY': 'testtestingg/doura-uptime', 'GITHUB_RUN_ID': '1'}), patch.object(monitor, 'github', side_effect=[[], {}, {}]) as api:
            monitor.notify({'checks': [{'ok': True}], 'checked_at': '2026-11-24'}, test=True, issue_number=12)
        self.assertEqual(api.call_args_list[-1].args[1], '/repos/testtestingg/doura-uptime/issues/12')
        self.assertEqual(api.call_args_list[-1].args[2]['state'], 'closed')

    def test_failure_never_copies_request_secret_into_report(self):
        with patch.object(monitor.time, 'sleep'):
            result = monitor.run_check('example', lambda: (_ for _ in ()).throw(ValueError('secret-value')))
        self.assertFalse(result['ok'])
        self.assertNotIn('secret-value', str(result))


if __name__ == '__main__':
    unittest.main()
