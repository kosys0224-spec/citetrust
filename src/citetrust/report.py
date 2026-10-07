"""Text, JSON, Markdown and SARIF reports."""
from __future__ import annotations

import json
import os
import sys
from collections import Counter
from typing import List

from . import __version__
from .model import Result, Status

_COLOR = {
    Status.VERIFIED: "\033[32m", Status.LIKELY: "\033[33m", Status.MISMATCH: "\033[31m", Status.NOT_FOUND: "\033[1;31m",
    Status.RETRACTED: "\033[1;97;41m", Status.DEAD_LINK: "\033[31m", Status.UNREACHABLE: "\033[36m", Status.SKIPPED: "\033[2m",
}
_LABEL = {
    Status.VERIFIED: "OK      ", Status.LIKELY: "LIKELY  ", Status.MISMATCH: "MISMATCH", Status.NOT_FOUND: "NOTFOUND",
    Status.RETRACTED: "RETRACTD", Status.DEAD_LINK: "DEADLINK", Status.UNREACHABLE: "UNKNOWN ", Status.SKIPPED: "SKIP    ",
}
_GLYPH_UTF = {Status.VERIFIED: "\u2713", Status.LIKELY: "?", Status.MISMATCH: "\u2717", Status.NOT_FOUND: "\u2717", Status.RETRACTED: "!", Status.DEAD_LINK: "\u2717", Status.UNREACHABLE: "~", Status.SKIPPED: "-"}
RESET = "\033[0m"
DIM = "\033[2m"
BOLD = "\033[1m"


def use_color(force: bool = False, disable: bool = False) -> bool:
    if disable or os.environ.get("NO_COLOR"):
        return False
    if force or os.environ.get("FORCE_COLOR"):
        return True
    try:
        return sys.stdout.isatty() and os.environ.get("TERM") != "dumb"
    except Exception:
        return False


def _c(code: str, s: str, on: bool) -> str:
    return "%s%s%s" % (code, s, RESET) if on else s


def _glyph(status: str) -> str:
    enc = (getattr(sys.stdout, "encoding", None) or "").lower()
    if "utf" in enc:
        return _GLYPH_UTF[status]
    return {"\u2713": "+", "\u2717": "x"}.get(_GLYPH_UTF[status], _GLYPH_UTF[status])


def summary_counts(results: List[Result]) -> Counter:
    return Counter(r.status for r in results)


def render_text(results: List[Result], color: bool = True, verbose: bool = False, only_problems: bool = False) -> str:
    out: List[str] = []
    files = sorted({r.ref.source for r in results})
    out.append(_c(BOLD, "citetrust %s" % __version__, color) + _c(DIM, "  %d reference(s) in %s" % (len(results), ", ".join(files) if files else "input"), color))
    out.append("")
    order = sorted(results, key=lambda r: (-Status.RANK[r.status], r.ref.source, r.ref.line or 0))
    shown = 0
    for r in order:
        if only_problems and r.status in (Status.VERIFIED, Status.SKIPPED):
            continue
        shown += 1
        loc = "%s:%s" % (os.path.basename(r.ref.source), r.ref.line) if r.ref.line else os.path.basename(r.ref.source)
        tag = _c(_COLOR[r.status], " %s " % _LABEL[r.status], color)
        out.append("%s %s %s" % (tag, _c(BOLD, r.ref.label(), color), _c(DIM, loc, color)))
        if r.ref.kind != "url" or verbose:
            out.append("         " + _c(DIM, _short(r.ref.raw, 96), color))
        for reason in r.reasons:
            out.append("         " + _glyph(r.status) + " " + reason)
        for s in r.suggestions:
            out.append("         " + _c("\033[32m", "\u2192 " + s, color))
        if verbose and r.checks:
            out.append("         " + _c(DIM, "checked: " + ", ".join(r.checks), color))
    if only_problems and shown == 0:
        out.append(_c("\033[32m", "  No problems found.", color))
    out.append("")
    counts = summary_counts(results)
    parts = []
    for st in (Status.VERIFIED, Status.LIKELY, Status.MISMATCH, Status.NOT_FOUND, Status.RETRACTED, Status.DEAD_LINK, Status.UNREACHABLE, Status.SKIPPED):
        if counts.get(st):
            parts.append(_c(_COLOR[st], "%d %s" % (counts[st], st), color))
    out.append("  " + _c(BOLD, "%d checked: " % len(results), color) + ", ".join(parts))
    bad = sum(counts.get(s, 0) for s in Status.BAD)
    if bad:
        out.append(_c(DIM, "  %d reference(s) need attention. Run with -v for the lookups performed, --json for details." % bad, color))
    return "\n".join(out)


def _short(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "\u2026"


def render_json(results: List[Result]) -> str:
    return json.dumps({"tool": "citetrust", "version": __version__, "summary": dict(summary_counts(results)), "results": [r.to_dict() for r in results]}, indent=2, ensure_ascii=False)


def render_markdown(results: List[Result]) -> str:
    counts = summary_counts(results)
    lines = ["## citetrust report", "", "| Status | Reference | Finding |", "|---|---|---|"]
    for r in sorted(results, key=lambda r: (-Status.RANK[r.status], r.ref.line or 0)):
        finding = "; ".join(r.reasons[:2]) if r.reasons else ""
        if r.suggestions:
            finding += " \u2192 " + r.suggestions[0]
        loc = "`%s:%s`" % (os.path.basename(r.ref.source), r.ref.line) if r.ref.line else ""
        lines.append("| **%s** | %s %s | %s |" % (r.status, _md_escape(r.ref.label()), loc, _md_escape(finding)))
    lines.append("")
    lines.append("**%d checked** \u2014 " % len(results) + ", ".join("%d %s" % (counts[s], s) for s in Status.ALL if counts.get(s)))
    return "\n".join(lines)


def _md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


_SARIF_LEVEL = {Status.RETRACTED: "error", Status.NOT_FOUND: "error", Status.MISMATCH: "error", Status.DEAD_LINK: "warning", Status.LIKELY: "note", Status.UNREACHABLE: "note"}


def render_sarif(results: List[Result]) -> str:
    rules = {}
    out = []
    for r in results:
        if r.status in (Status.VERIFIED, Status.SKIPPED):
            continue
        rid = "citetrust/" + r.status
        rules.setdefault(rid, {"id": rid, "shortDescription": {"text": "Reference %s" % r.status}, "defaultConfiguration": {"level": _SARIF_LEVEL[r.status]}})
        loc = {"physicalLocation": {"artifactLocation": {"uri": r.ref.source.replace(os.sep, "/"), "uriBaseId": "%SRCROOT%"}}}
        if r.ref.line:
            loc["physicalLocation"]["region"] = {"startLine": r.ref.line}
        out.append({"ruleId": rid, "level": _SARIF_LEVEL[r.status], "message": {"text": "%s: %s" % (r.ref.label(), "; ".join(r.reasons + r.suggestions))}, "locations": [loc]})
    doc = {"$schema": "https://json.schemastore.org/sarif-2.1.0.json", "version": "2.1.0",
           "runs": [{"tool": {"driver": {"name": "citetrust", "version": __version__, "informationUri": "https://github.com/kosys0224-spec/citetrust", "rules": list(rules.values())}}, "results": out}]}
    return json.dumps(doc, indent=2)
