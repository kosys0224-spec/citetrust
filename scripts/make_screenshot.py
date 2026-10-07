#!/usr/bin/env python3
"""Render `citetrust` terminal output as docs/demo.png (needs Playwright + Chromium; dev-only)."""
from __future__ import annotations

import html
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PALETTE = {"30": "#555", "31": "#ff6b6b", "32": "#5af78e", "33": "#f3f99d", "34": "#57c7ff", "35": "#ff6ac1", "36": "#9aedfe", "37": "#c7c7c7", "90": "#777", "97": "#ffffff"}
BG = {"41": "#c0392b"}


def ansi_to_html(text: str) -> str:
    out = []
    state = {"color": None, "bg": None, "bold": False, "dim": False}
    span_open = False

    def open_span():
        nonlocal span_open
        if span_open:
            out.append("</span>")
        styles = []
        if state["color"]:
            styles.append("color:%s" % state["color"])
        if state["bg"]:
            styles.append("background:%s;padding:0 2px;border-radius:3px" % state["bg"])
        if state["bold"]:
            styles.append("font-weight:700")
        if state["dim"]:
            styles.append("opacity:.6")
        out.append('<span style="%s">' % ";".join(styles))
        span_open = True

    pos = 0
    for m in re.finditer(r"\x1b\[([0-9;]*)m", text):
        out.append(html.escape(text[pos: m.start()]))
        pos = m.end()
        codes = m.group(1).split(";") if m.group(1) else ["0"]
        for c in codes:
            if c in ("0", ""):
                state.update(color=None, bg=None, bold=False, dim=False)
            elif c == "1":
                state["bold"] = True
            elif c == "2":
                state["dim"] = True
            elif c in PALETTE:
                state["color"] = PALETTE[c]
            elif c in BG:
                state["bg"] = BG[c]
        open_span()
    out.append(html.escape(text[pos:]))
    if span_open:
        out.append("</span>")
    return "".join(out)


def main() -> int:
    demo = Path(tempfile.mkdtemp(prefix="citetrust-demo-"))
    env = {"FORCE_COLOR": "1", "PYTHONPATH": str(ROOT / "src") + os.pathsep + str(ROOT / "tests"), "PATH": "/usr/bin:/bin"}
    code = ("import sys\nfrom replay import replay_fetch\nfrom citetrust.cli import main\n"
            "sys.exit(main(['examples/sample.md', '--color'], fetch=replay_fetch))")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env, cwd=str(ROOT))
    body = proc.stdout
    prompt = '<span style="color:#5af78e">$</span> citetrust examples/sample.md\n\n'
    page = """<!doctype html><html><body style="margin:0;background:#1e1f29">
<div style="width:980px;background:#282a36;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,.6);overflow:hidden;font-family:'JetBrains Mono','Fira Code',Menlo,Consolas,monospace;font-size:13.5px;line-height:1.45">
<div style="height:36px;background:#21222c;display:flex;align-items:center;padding:0 14px;gap:8px">
<span style="width:12px;height:12px;border-radius:50%%;background:#ff5f56;display:inline-block"></span>
<span style="width:12px;height:12px;border-radius:50%%;background:#ffbd2e;display:inline-block"></span>
<span style="width:12px;height:12px;border-radius:50%%;background:#27c93f;display:inline-block"></span>
<span style="color:#888;margin-left:12px;font-size:12px">citetrust</span></div>
<pre style="margin:0;padding:18px 22px;color:#f8f8f2;white-space:pre-wrap;word-break:break-all">%s%s</pre></div></body></html>""" % (prompt, ansi_to_html(body))
    html_path = demo / "demo.html"
    html_path.write_text(page, encoding="utf-8")
    from playwright.sync_api import sync_playwright

    out = ROOT / "docs" / "demo.png"
    with sync_playwright() as p:
        browser = p.chromium.launch()
        pg = browser.new_page(viewport={"width": 1040, "height": 1400}, device_scale_factor=2)
        pg.goto(html_path.as_uri())
        el = pg.query_selector("div")
        el.screenshot(path=str(out))
        browser.close()
    print("wrote", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
