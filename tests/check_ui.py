"""Playwright UI verification: every route renders, Alpine boots from the
bundled copy (no CDN), and no page overflows horizontally or raises a
JavaScript error at desktop and narrow viewport widths."""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from test_transfer import server  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

http = server.ThreadingHTTPServer(('127.0.0.1', 0), server.SyncHandler)
http.daemon_threads = True
threading.Thread(target=http.serve_forever, daemon=True).start()

BASE = f'http://127.0.0.1:{http.server_port}'
ROUTES = ['/', 'categories', 'history', 'settings', 'monitor', 'diff', 'audit', 'backup']
WIDTHS = (1280, 960, 760, 420)

errors = []
with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    page = browser.new_page()
    page.on('pageerror', lambda e: errors.append('pageerror: ' + str(e)))
    page.on('console', lambda m: errors.append('console.error: ' + m.text)
            if m.type == 'error' else None)
    page.on('requestfailed', lambda r: errors.append('requestfailed: ' + r.url))

    for width in WIDTHS:
        page.set_viewport_size({'width': width, 'height': 820})
        for route in ROUTES:
            response = page.goto(f'{BASE}/{route}', wait_until='domcontentloaded')
            assert response.status == 200, f'{route} @ {width}px -> HTTP {response.status}'
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), \
                f'horizontal overflow on {route} @ {width}px: ' \
                f'scrollWidth={page.evaluate("document.documentElement.scrollWidth")} > {width}'
            page.wait_for_timeout(250)
        # Alpine must come from the local bundle, and the store must be live.
        page.goto(f'{BASE}/', wait_until='domcontentloaded')
        page.wait_for_function('window.Alpine && Alpine.store("sync")')
        script_srcs = page.evaluate('[...document.scripts].map(s => s.src)')
        alpine_src = next((s for s in script_srcs if 'alpine' in s), '')
        assert alpine_src.endswith('/static/alpinejs.min.js'), \
            'Alpine not loaded from local bundle: ' + alpine_src
        assert not any('cdn.' in s for s in script_srcs), \
            'page still references a CDN script'
        page.wait_for_timeout(400)
        assert page.locator('.task-select').is_visible(), f'task-select hidden @ {width}px'
        print(f'OK viewport {width}px: {len(ROUTES)} routes, no overflow, Alpine store live')

    browser.close()
http.shutdown()

assert not errors, 'JavaScript/network errors: ' + '; '.join(errors)
print(f'No JavaScript errors across {len(WIDTHS) * len(ROUTES)} page loads')
