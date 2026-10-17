"""Turning a flyer's HTML into a PNG with headless Chrome.

Chrome is called directly: nothing to install in Python, and the page is drawn by a real browser engine, so the fonts
and text wrapping come out right. It is Google Chrome, Chromium or Edge, whichever is on this machine.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

WIDTH, HEIGHT = 1080, 1350  # 4:5, the shape that shows whole in WhatsApp and phone feeds

CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "google-chrome",
    "google-chrome-stable",
    "chromium",
    "chromium-browser",
    "microsoft-edge",
)


class FlyerError(Exception):
    """The flyer couldn't be drawn (no browser, the browser failed). Not about the request itself."""


def find_chrome(configured: str | None = None) -> str:
    if configured:
        found = shutil.which(configured)
        if not found:
            raise FlyerError(f"SETH_CHROME={configured!r} is not an executable")
        return found
    for name in CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    raise FlyerError("no Chrome, Chromium or Edge found: install Google Chrome, or set SETH_CHROME to its path")


def render_png(html: str, out: Path, chrome: str | None = None) -> None:
    binary = find_chrome(chrome)
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="seth-flyer-") as tmp:
        page = Path(tmp) / "flyer.html"
        page.write_text(html, encoding="utf-8")
        shot = Path(tmp) / "flyer.png"
        # A throwaway profile, so this never joins (or disturbs) a Chrome the user has open.
        cmd = [
            binary, "--headless=new", "--disable-gpu", "--hide-scrollbars", "--no-first-run",
            f"--user-data-dir={Path(tmp) / 'profile'}", "--force-device-scale-factor=1",
            f"--window-size={WIDTH},{HEIGHT}", f"--screenshot={shot}", page.as_uri(),
        ]  # fmt: skip
        try:
            subprocess.run(cmd, capture_output=True, timeout=60, check=False)
        except subprocess.TimeoutExpired as e:
            raise FlyerError("Chrome took more than 60 seconds to draw the flyer") from e
        except OSError as e:
            raise FlyerError(f"couldn't start {binary}: {e}") from e
        if not shot.is_file() or shot.stat().st_size == 0:
            raise FlyerError(f"Chrome ({binary}) didn't produce a picture")
        shutil.move(shot, out)


CHECK_SCRIPT = r"""<script>
window.addEventListener('load', function () {
  var W = 1080, H = 1350, issues = [], boxes = [];
  document.querySelectorAll('body *').forEach(function (el) {
    var cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden') return;
    var own = Array.prototype.some.call(el.childNodes, function (n) { return n.nodeType === 3 && n.textContent.trim().length > 0; });
    if (!own || el.closest('svg')) return;
    var r = el.getBoundingClientRect(), txt = el.textContent.trim().replace(/\s+/g, ' ').slice(0, 28);
    if (r.right > W + 2 || r.bottom > H + 2 || r.left < -2 || r.top < -2) issues.push('the text "' + txt + '" runs off the page (it spans x ' + Math.round(r.left) + '-' + Math.round(r.right) + ', y ' + Math.round(r.top) + '-' + Math.round(r.bottom) + '; the page is 1080 x 1350). Make everything above it shorter or smaller so it fits');
    if (parseFloat(cs.fontSize) < 18) issues.push('the text "' + txt + '" is smaller than 18px');
    boxes.push({ el: el, r: r, txt: txt });
  });
  for (var i = 0; i < boxes.length; i++) for (var j = i + 1; j < boxes.length; j++) {
    var a = boxes[i], b = boxes[j];
    if (a.el.contains(b.el) || b.el.contains(a.el)) continue;
    var w = Math.min(a.r.right, b.r.right) - Math.max(a.r.left, b.r.left), h = Math.min(a.r.bottom, b.r.bottom) - Math.max(a.r.top, b.r.top);
    if (w > 6 && h > 6 && w * h > 0.2 * Math.min(a.r.width * a.r.height, b.r.width * b.r.height)) issues.push('the text "' + a.txt + '" overlaps the text "' + b.txt + '"');
  }
  boxes.forEach(function (b) {  // text with a picture, tape or card painted over it
    var range = document.createRange();
    for (var k = 0; k < b.el.childNodes.length; k++) {
      var n = b.el.childNodes[k];
      if (n.nodeType !== 3 || !n.textContent.trim()) continue;
      range.selectNodeContents(n);
      var rects = range.getClientRects();
      if (!rects.length) continue;
      var q = rects[0], top = document.elementFromPoint(q.left + q.width / 2, q.top + q.height / 2);
      if (top && top !== b.el && !b.el.contains(top) && !top.contains(b.el)) { issues.push('the text "' + b.txt + '" is covered by something on top of it (a photo, shape or card)'); break; }
    }
  });
  document.querySelectorAll('img').forEach(function (img) {  // a photo or the logo with a decoration painted over it
    var t = (img.getAttribute('src') || '').split('#')[1] || '', r0 = img.getBoundingClientRect();
    if ((t === 'photo' || t.indexOf('logo') === 0) && r0.width > 0) {
      var pts = [[0.5, 0.5], [0.25, 0.3], [0.75, 0.3], [0.25, 0.7], [0.75, 0.7]], hidden = 0;
      pts.forEach(function (p) {
        var top = document.elementFromPoint(r0.left + r0.width * p[0], r0.top + r0.height * p[1]);
        if (top && top !== img && !img.contains(top)) hidden++;
      });
      if (hidden > 0) issues.push('a ' + (t === 'photo' ? 'photo' : 'logo') + ' is partly covered by something painted over it (a decoration, shape or text); keep photos and the logo clear');
    }
  });
  document.querySelectorAll('img').forEach(function (img) {
    var tag = (img.getAttribute('src') || '').split('#')[1] || '', r = img.getBoundingClientRect();
    if (!img.complete || img.naturalWidth === 0) issues.push('a picture did not load');
    if (tag.indexOf('logo') === 0 && r.width < 300) issues.push('the logo is too small (' + Math.round(r.width) + 'px wide; it needs at least 320px, so make room by shrinking other things, not the logo)');
    if (tag === 'photo' && (r.width < 170 || r.height < 130)) issues.push('a photo is too small (' + Math.round(r.width) + 'x' + Math.round(r.height) + 'px)');
    if (tag && tag !== 'motif' && (r.right < 20 || r.left > W - 20 || r.bottom < 20 || r.top > H - 20)) issues.push('a picture is mostly off the page');
  });
  document.documentElement.setAttribute('data-seth-report', JSON.stringify(issues.slice(0, 12)));
});
</script>"""


def check_page(html: str, chrome: str | None = None) -> list[str]:
    """Draws the page and reports what looks wrong: text off the page or overlapping, tiny text, a missing or tiny logo or photo."""
    import json
    import re
    from html import unescape

    binary = find_chrome(chrome)
    page_html = re.sub(r"</body>", lambda m: CHECK_SCRIPT + "</body>", html, count=1, flags=re.I) if re.search(r"</body>", html, re.I) else html + CHECK_SCRIPT
    with tempfile.TemporaryDirectory(prefix="seth-flyer-") as tmp:
        page = Path(tmp) / "check.html"
        page.write_text(page_html, encoding="utf-8")
        cmd = [
            binary, "--headless=new", "--disable-gpu", "--no-first-run", f"--user-data-dir={Path(tmp) / 'profile'}",
            f"--window-size={WIDTH},{HEIGHT}", "--virtual-time-budget=4000", "--dump-dom", page.as_uri(),
        ]  # fmt: skip
        try:
            done = subprocess.run(cmd, capture_output=True, timeout=90, check=False, text=True)
        except subprocess.TimeoutExpired as e:
            raise FlyerError("Chrome took more than 90 seconds to check the flyer") from e
        except OSError as e:
            raise FlyerError(f"couldn't start {binary}: {e}") from e
    found = re.search(r'data-seth-report="([^"]*)"', done.stdout)
    if not found:
        return ["the page didn't finish drawing (is its HTML broken?)"]
    return list(dict.fromkeys(json.loads(unescape(found.group(1)))))
