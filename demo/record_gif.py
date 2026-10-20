"""Record an animated GIF of the Streamlit demo (demo/streamlit_app.py) for a README or a portfolio page.

    python demo/record_gif.py --out ../demo/canada_desk.gif

Starts the app on a local port, drives a headless Chrome or Edge through the Chrome DevTools Protocol, waits until
each view has finished rendering, takes a screenshot, and joins the screenshots into a GIF with Pillow. Standard
library plus Pillow and Streamlit; no network beyond localhost. Each frame is a URL of the app
(?task=...&view=...&round=...), so the GIF is reproducible.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

APP = Path(__file__).resolve().parent / "streamlit_app.py"
TASK = "c03_big5_q3_sector_shared"
# (query, seconds on screen, element id to scroll to)
FRAMES = [
    ({"view": "Task"}, 2.6, None),
    *[({"view": "Desk replay", "round": r}, 1.1, None) for r in (2, 6, 10, 11, 13, 15)],
    ({"view": "Desk replay", "round": 18}, 2.4, None),
    ({"view": "Big 5 data"}, 3.6, None),
    ({"view": "Sector note"}, 2.0, None),
    ({"view": "Sector note"}, 3.6, "comparison"),
    ({"view": "Sector note"}, 3.2, "how-each-answer-was-derived"),
]
BROWSERS = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "google-chrome", "chromium", "chromium-browser", "microsoft-edge",
]
READY_JS = """(() => {
  if (document.readyState !== 'complete') return false;
  if (document.querySelector('[data-testid="stStatusWidget"]')) return false;   // script still running
  if (document.querySelector('[data-testid="stSkeleton"]')) return false;
  const main = document.querySelector('[data-testid="stMain"]') || document.body;
  return main.innerText.length > 200;
})()"""


def find_browser(explicit: str | None) -> str:
    for cand in [explicit, os.environ.get("CHROME_PATH"), *BROWSERS]:
        if cand and (Path(cand).exists() or shutil.which(cand)):
            return shutil.which(cand) or cand
    sys.exit("No Chrome or Edge found; pass --browser PATH or set CHROME_PATH.")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_http(url: str, timeout: float = 60) -> bytes:
    end = time.time() + timeout
    while time.time() < end:
        try:
            with urllib.request.urlopen(url, timeout=2) as r:
                return r.read()
        except OSError:
            time.sleep(0.3)
    raise TimeoutError(f"{url} did not answer within {timeout:.0f}s")


class DevTools:
    """A minimal Chrome DevTools Protocol client over a WebSocket (RFC 6455), enough for a few commands."""

    def __init__(self, ws_url: str) -> None:
        u = urllib.parse.urlparse(ws_url)
        self.sock = socket.create_connection((u.hostname, u.port), timeout=60)
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall((f"GET {u.path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.sock.recv(1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode(errors="replace"))
        self.next_id = 0

    def _recv_exact(self, n: int) -> bytes:
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("DevTools socket closed")
            buf += chunk
        return buf

    def _recv_message(self) -> str:
        data = b""
        while True:
            b0, b1 = self._recv_exact(2)
            n = b1 & 0x7F
            if n == 126:
                n = int.from_bytes(self._recv_exact(2), "big")
            elif n == 127:
                n = int.from_bytes(self._recv_exact(8), "big")
            payload = self._recv_exact(n)
            if b0 & 0x0F == 0x9:  # ping
                continue
            data += payload
            if b0 & 0x80:
                return data.decode("utf-8")

    def call(self, method: str, **params) -> dict:
        self.next_id += 1
        payload = json.dumps({"id": self.next_id, "method": method, "params": params}).encode()
        n = len(payload)
        header = bytes([0x81]) + (bytes([0x80 | n]) if n < 126 else bytes([0x80 | 126]) + n.to_bytes(2, "big")
                                  if n < 65536 else bytes([0x80 | 127]) + n.to_bytes(8, "big"))
        mask = os.urandom(4)
        self.sock.sendall(header + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))
        while True:
            msg = json.loads(self._recv_message())
            if msg.get("id") == self.next_id:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    def evaluate(self, expression: str):
        return self.call("Runtime.evaluate", expression=expression, returnByValue=True).get("result", {}).get("value")


def capture(dev: DevTools, url: str, anchor: str | None, settle: float, timeout: float = 60) -> Image.Image:
    dev.call("Page.navigate", url=url)
    time.sleep(1.0)
    end = time.time() + timeout
    while not dev.evaluate(READY_JS):
        if time.time() > end:
            raise TimeoutError(f"{url} did not finish rendering")
        time.sleep(0.3)
    if anchor:
        dev.evaluate(f"document.getElementById({json.dumps(anchor)})?.scrollIntoView({{block: 'start'}})")
    time.sleep(settle)  # charts and fonts draw after the script finishes
    png = base64.b64decode(dev.call("Page.captureScreenshot", format="png")["data"])
    return Image.open(io.BytesIO(png)).convert("RGB")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True, help="GIF to write")
    ap.add_argument("--task", default=TASK)
    ap.add_argument("--theme", choices=["dark", "light"], default="dark")
    ap.add_argument("--width", type=int, default=1280)
    ap.add_argument("--height", type=int, default=860)
    ap.add_argument("--scale", type=float, default=0.75, help="resize frames to make the GIF smaller")
    ap.add_argument("--browser", help="Chrome or Edge executable (default: search common locations)")
    ap.add_argument("--frames-dir", help="also keep each frame as a PNG here")
    args = ap.parse_args(argv)

    browser = find_browser(args.browser)
    app_port, cdp_port = free_port(), free_port()
    profile = tempfile.mkdtemp(prefix="awl-gif-")
    server = subprocess.Popen([sys.executable, "-m", "streamlit", "run", str(APP), "--server.headless", "true",
                               "--server.port", str(app_port), "--browser.gatherUsageStats", "false",
                               "--client.toolbarMode", "viewer", "--theme.base", args.theme],
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    chrome = None
    try:
        wait_http(f"http://127.0.0.1:{app_port}/_stcore/health")
        chrome = subprocess.Popen([browser, "--headless=new", f"--remote-debugging-port={cdp_port}",
                                   f"--user-data-dir={profile}", "--hide-scrollbars", "--no-first-run",
                                   "--disable-extensions", f"--window-size={args.width},{args.height}", "about:blank"],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        wait_http(f"http://127.0.0.1:{cdp_port}/json/version")
        pages = [t for t in json.loads(wait_http(f"http://127.0.0.1:{cdp_port}/json/list")) if t["type"] == "page"]
        dev = DevTools(pages[0]["webSocketDebuggerUrl"])
        dev.call("Page.enable")
        dev.call("Emulation.setDeviceMetricsOverride", width=args.width, height=args.height, deviceScaleFactor=1,
                 mobile=False)
        frames, durations = [], []
        for i, (query, seconds, anchor) in enumerate(FRAMES):
            url = f"http://127.0.0.1:{app_port}/?" + urllib.parse.urlencode({"task": args.task, **query})
            img = capture(dev, url, anchor, settle=1.5 if i == 0 else 0.8)
            if args.scale != 1:
                img = img.resize((round(img.width * args.scale), round(img.height * args.scale)), Image.LANCZOS)
            if args.frames_dir:
                Path(args.frames_dir).mkdir(parents=True, exist_ok=True)
                img.save(Path(args.frames_dir) / f"frame_{i:02d}.png")
            frames.append(img)
            durations.append(int(seconds * 1000))
            print(f"frame {i + 1}/{len(FRAMES)}: {query}" + (f" #{anchor}" if anchor else ""))
    finally:
        for proc in (chrome, server):
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
        shutil.rmtree(profile, ignore_errors=True)

    # one palette for all frames, built from a strip of every frame, so colours do not shift between frames
    thumbs = [f.resize((f.width // 2, f.height // 2)) for f in frames]
    strip = Image.new("RGB", (thumbs[0].width, sum(t.height for t in thumbs)))
    for i, t in enumerate(thumbs):
        strip.paste(t, (0, i * thumbs[0].height))
    palette = strip.quantize(colors=255, method=Image.Quantize.MEDIANCUT)
    paletted = [f.quantize(palette=palette, dither=Image.Dither.NONE) for f in frames]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    paletted[0].save(out, save_all=True, append_images=paletted[1:], duration=durations, loop=0, optimize=True)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.1f} MB, {len(frames)} frames, {sum(durations) / 1000:.0f} s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
