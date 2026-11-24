"""Public, read-only Doura checks. No application credentials or customer data."""
import concurrent.futures
import datetime as dt
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

ORIGIN = 'https://www.doura.tn'
API = 'https://wbufoavhbppkzlthucjr.supabase.co'
MARKER = '<!-- doura-uptime-incident-v1 -->'
USER_AGENT = 'Doura-Uptime/1.0 (+https://github.com/testtestingg/doura-uptime)'


class SafeRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        old = urllib.parse.urlsplit(req.full_url)
        new = urllib.parse.urlsplit(newurl)
        same = old.netloc == new.netloc
        canonical = old.netloc == 'doura.tn' and new.netloc == 'www.doura.tn'
        if new.scheme != 'https' or not (same or canonical):
            raise ValueError('Unexpected redirect destination')
        # Python 3.9 does not handle 308 by default. GET/HEAD retain their
        # method for this permanent canonical redirect, as a browser does.
        return super().redirect_request(req, fp, 307 if code == 308 else code, msg, headers, newurl)

    http_error_308 = urllib.request.HTTPRedirectHandler.http_error_302


class Page(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.assets = []
        self.key = None
        self.root = False
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'div' and attrs.get('id') == 'root':
            self.root = True
        if tag == 'script':
            src = attrs.get('src', '')
            if src.startswith('/assets/') and src.endswith('.js'):
                self.assets.append(src)
            if src == '/home-hero-bootstrap.js' and attrs.get('data-origin') == API:
                self.key = attrs.get('data-key')
        if tag == 'link' and attrs.get('rel') == 'stylesheet':
            src = attrs.get('href', '')
            if src.startswith('/assets/'):
                self.assets.append(src)


def fetch(url, payload=None, headers=None):
    request = urllib.request.Request(url,
        data=json.dumps(payload).encode() if payload is not None else None,
        headers={'User-Agent': USER_AGENT, **(headers or {})})
    with urllib.request.build_opener(SafeRedirects()).open(request, timeout=12) as response:
        body = response.read(2_000_001)
        if len(body) > 2_000_000:
            raise ValueError('Response exceeded monitor size limit')
        return response.status, response.url, response.headers.get('Content-Type', ''), body


def html_check(url):
    status, final, mime, body = fetch(url)
    if status != 200 or urllib.parse.urlsplit(final).netloc != 'www.doura.tn':
        raise ValueError('Unexpected status or redirect destination')
    page = Page(body.decode())
    if 'text/html' not in mime or not page.root or not page.assets:
        raise ValueError('Expected Doura app shell and compiled assets')
    return page


def json_check(path, key, payload=None):
    status, _, mime, body = fetch(API + path, payload,
        {'apikey': key, 'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json'})
    if status != 200 or 'application/json' not in mime:
        raise ValueError('Expected successful JSON API response')
    return json.loads(body)


def run_check(name, operation):
    start = time.monotonic()
    # Confirm failures once to reduce alerts from a transient packet loss.
    for attempt in range(2):
        try:
            operation()
            return {'name': name, 'ok': True, 'ms': round((time.monotonic() - start) * 1000)}
        except Exception as error:
            if attempt == 0:
                time.sleep(2)
            else:
                # Never print response bodies, request headers or credentials.
                reason = f'HTTP {error.code}' if isinstance(error, urllib.error.HTTPError) else type(error).__name__
                return {'name': name, 'ok': False, 'error': reason,
                        'ms': round((time.monotonic() - start) * 1000)}


def collect():
    page = None
    results = []
    def load_home():
        nonlocal page
        page = html_check(ORIGIN + '/')
    results.append(run_check('www homepage and TLS', load_home))
    checks = [('apex redirect and TLS', lambda: html_check('https://doura.tn/'))]
    for path in ['/annonces', '/neuf', '/neuf/kia', '/guide/droits-de-douane', '/actualite', '/connecter']:
        checks.append((path, lambda path=path: html_check(ORIGIN + path)))
    def sitemap():
        status, _, mime, body = fetch(ORIGIN + '/sitemap.xml')
        if status != 200 or 'xml' not in mime or b'<sitemapindex' not in body:
            raise ValueError('Expected sitemap index XML')
    checks.append(('sitemap API', sitemap))
    if page:
        for path in page.assets:
            def asset(path=path):
                status, _, mime, body = fetch(ORIGIN + path)
                if status != 200 or not body or 'text/html' in mime:
                    raise ValueError('Compiled asset missing or HTML fallback')
            checks.append(('compiled asset ' + path, asset))
        if page.key:
            def listings():
                data = json_check('/rest/v1/rpc/get_public_occasion_page', page.key,
                                  {'p_filters': {}, 'p_limit': 1, 'p_offset': 0})
                if not isinstance(data.get('listings'), list) or not isinstance(data.get('total'), int) or data['total'] < 1 or not data['listings']:
                    raise ValueError('Expected published listings')
            def catalogue():
                data = json_check('/rest/v1/rpc/search_public_vehicle_catalogue', page.key,
                                  {'p_query': 'Kia', 'p_limit': 1, 'p_offset': 0})
                if not isinstance(data, list) or not data:
                    raise ValueError('Expected catalogue search matches')
            def auth():
                data = json_check('/auth/v1/settings', page.key)
                if not isinstance(data.get('external'), dict):
                    raise ValueError('Expected Auth configuration')
            checks.extend([('published listings RPC', listings), ('catalogue search RPC', catalogue), ('Auth service settings', auth)])
        else:
            results.append({'name': 'public API bootstrap', 'ok': False, 'error': 'MissingPublicConfiguration'})
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results.extend(executor.map(lambda check: run_check(*check), checks))
    return results


def incident_action(results, existing):
    failed = any(not result['ok'] for result in results)
    if failed and not existing:
        return 'open'
    if not failed and existing:
        return 'recover'
    return 'none'


def github(method, path, payload=None):
    token = os.environ['GH_TOKEN']
    request = urllib.request.Request('https://api.github.com' + path,
        data=json.dumps(payload).encode() if payload is not None else None, method=method,
        headers={'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json',
                 'Content-Type': 'application/json', 'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.load(response)


def notify(report, test=False):
    repo = os.environ['GITHUB_REPOSITORY']
    # Only this separate public monitor repository is permitted.
    if repo != 'testtestingg/doura-uptime':
        raise ValueError('Unexpected monitoring repository')
    marker = MARKER + ('<!-- alert-test -->' if test else '<!-- production -->')
    issues = github('GET', f'/repos/{repo}/issues?state=open&creator=github-actions%5Bbot%5D&per_page=100')
    existing = next((item for item in issues if marker in (item.get('body') or '')), None)
    action = incident_action(report['checks'], existing)
    run = f"https://github.com/{repo}/actions/runs/{os.environ['GITHUB_RUN_ID']}"
    if action == 'open':
        failures = '\n'.join(f"- {row['name']}: {row.get('error', 'failed')}" for row in report['checks'] if not row['ok'])
        title = '[TEST] Doura uptime alert delivery' if test else '[OUTAGE] Doura production check failed'
        body = f"{marker}\n\n@{repo.split('/')[0]} {'Monitor delivery test; production is not reported down.' if test else 'A public production check failed twice.'}\n\nFirst confirmed check failure: {report['checked_at']} (UTC).\n\n{failures}\n\n[Workflow evidence]({run})\n\nThe outage may have begun between the previous successful check and this check."
        issue = github('POST', f'/repos/{repo}/issues', {'title': title, 'body': body, 'assignees': [repo.split('/')[0]]})
        return issue['number']
    if action == 'recover':
        github('POST', f"/repos/{repo}/issues/{existing['number']}/comments",
               {'body': f"@{repo.split('/')[0]} {'Delivery test completed' if test else 'Recovery confirmed'} at {report['checked_at']} UTC. All checks passed. [Workflow evidence]({run})"})
        github('PATCH', f"/repos/{repo}/issues/{existing['number']}", {'state': 'closed', 'state_reason': 'completed'})
    return None


if __name__ == '__main__':
    test = os.environ.get('ALERT_TEST') == 'true'
    report = {'checked_at': dt.datetime.now(dt.timezone.utc).isoformat(), 'checks': collect()}
    Path('report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    if os.environ.get('GH_TOKEN'):
        if test:
            test_report = {**report, 'checks': [{'name': 'intentional alert delivery test', 'ok': False, 'error': 'TEST'}]}
            notify(test_report, test=True)
            notify({**report, 'checks': [{'name': 'intentional recovery delivery test', 'ok': True}]}, test=True)
        else:
            notify(report)
    raise SystemExit(0 if all(row['ok'] for row in report['checks']) else 1)
