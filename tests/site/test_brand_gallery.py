"""Brand gallery loading hints and reserved image geometry (#258); local files only."""
import functools
import http.server
import importlib.util
import threading
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SITE = ROOT / 'site'


class Images(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_main = False
        self.gallery = []

    def handle_starttag(self, tag, attrs):
        if tag == 'main':
            self.in_main = True
        if tag == 'img' and self.in_main:
            self.gallery.append(dict(attrs))

    def handle_endtag(self, tag):
        if tag == 'main':
            self.in_main = False


def gallery():
    parsed = Images()
    parsed.feed((SITE / 'brand/index.html').read_text())
    return parsed.gallery


def test_gallery_reserves_positive_dimensions_and_defers_below_fold_images():
    images = gallery()
    assert images
    for image in images:
        assert image.get('loading') == 'lazy', image['src']
        assert image.get('decoding') == 'async', image['src']
        assert image.get('width', '').isdigit() and int(image['width']) > 0, image['src']
        assert image.get('height', '').isdigit() and int(image['height']) > 0, image['src']
        assert (SITE / 'brand' / image['src']).is_file()


def browser_module():
    spec = importlib.util.spec_from_file_location('brand_gallery_browser', ROOT / 'plugins/exo-command/tests/browser.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('width', [1440, 420])
def test_image_boxes_are_reserved_before_fetch_and_stable_after_decode(width):
    module = browser_module()
    if not module.find_chrome() or not importlib.util.find_spec('websockets'):
        pytest.skip('headless Chrome or websockets unavailable')
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(SITE))
    class GalleryServer(http.server.ThreadingHTTPServer):
        request_queue_size = 128
        daemon_threads = True

    server = GalleryServer(('127.0.0.1', 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with module.Browser(width=width, height=900) as browser:
            browser.call('Network.enable')
            browser.call('Network.setBlockedURLs', urls=['*.svg', '*.png'])
            browser.goto(f'http://127.0.0.1:{server.server_port}/brand/')
            browser.wait(f"document.querySelectorAll('main img').length === {len(gallery())}")
            # Settle the deliberately blocked requests before attaching fresh load handlers.
            browser.eval("document.querySelectorAll('main img').forEach(i=>i.loading='eager')")
            browser.wait("[...document.querySelectorAll('main img')].every(i=>i.complete)")
            browser.eval('document.fonts.ready')
            measure = "[...document.querySelectorAll('main img')].map(i=>{const r=i.getBoundingClientRect();return {src:new URL(i.src).pathname,x:r.x,y:r.y,w:r.width,h:r.height,natural:i.naturalWidth}})"
            before = browser.eval(measure)
            assert all(r['natural'] == 0 for r in before), before
            assert all(r['w'] > 0 and r['h'] > 0 for r in before), before
            browser.call('Network.setBlockedURLs', urls=[])
            loaded = browser.eval("""Promise.all([...document.querySelectorAll('main img')].map((old,n)=>new Promise(resolve=>{
                const image=old.cloneNode(false);
                image.onload=async()=>{try{await image.decode();resolve({src:image.src,ok:image.naturalWidth>0})}
                    catch(error){resolve({src:image.src,ok:false,error:String(error)})}};
                image.onerror=()=>resolve({src:image.src,ok:false,error:'load failed'});
                image.loading='eager';image.src=old.src+'?geometry-test='+n;
                old.replaceWith(image);
            })))""")
            assert all(row['ok'] for row in loaded), [row for row in loaded if not row['ok']]
            after = browser.eval(measure)
            for a, b in zip(before, after, strict=True):
                assert a['src'] == b['src']
                assert all(abs(a[k] - b[k]) <= 1 for k in ['x', 'y', 'w', 'h']), (a, b)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
