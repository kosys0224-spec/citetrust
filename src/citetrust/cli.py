"""Command-line interface for citetrust."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__, cache, sources
from .extract import extract_from_text, extract_references
from .model import Status
from .report import render_json, render_markdown, render_sarif, render_text, summary_counts, use_color
from .verify import verify_references

EXIT_OK = 0
EXIT_PROBLEMS = 1
EXIT_ERROR = 2

SUPPORTED = (".md", ".markdown", ".txt", ".rst", ".tex", ".bib", ".docx", ".html", ".htm")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="citetrust",
        description="Check that the references in a document exist, match what the document claims (title, authors, year), "
                    "are not retracted, and that cited URLs are alive. Reads Markdown, text, reST, LaTeX, HTML, DOCX and BibTeX.",
        epilog="Examples:\n  citetrust paper.md\n  citetrust thesis.docx --only-problems\n  citetrust refs.bib --json > report.json\n"
               "  citetrust docs/ --sarif > citetrust.sarif        # directory: all supported files\n  cat refs.txt | citetrust -\n",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("paths", nargs="*", help="files or directories to check; '-' reads stdin")
    p.add_argument("--list", action="store_true", help="only extract and list references, no network")
    p.add_argument("--only-problems", action="store_true", help="hide verified references in the text report")
    p.add_argument("--json", action="store_true", help="JSON output")
    p.add_argument("--markdown", action="store_true", help="Markdown table (for PR comments)")
    p.add_argument("--sarif", action="store_true", help="SARIF 2.1.0 (GitHub code scanning)")
    p.add_argument("-v", "--verbose", action="store_true", help="show which lookups were performed")
    p.add_argument("--fail-on", default="mismatch", metavar="LEVEL",
                   help="exit 1 when any result is at/above this: retracted, not-found, mismatch (default), dead-link, likely, never")
    p.add_argument("--no-urls", action="store_true", help="do not check plain URLs")
    p.add_argument("--no-cache", action="store_true", help="ignore the 7-day lookup cache")
    p.add_argument("--workers", type=int, default=6, help="parallel lookups (default 6)")
    p.add_argument("--mailto", metavar="EMAIL", help="email sent to Crossref/OpenAlex 'polite pool' (faster, recommended); env CITETRUST_MAILTO")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--color", action="store_true")
    p.add_argument("--clear-cache", action="store_true", help="delete cached lookups and exit")
    p.add_argument("--version", action="version", version="citetrust %s" % __version__)
    return p


def _collect(paths: List[str]) -> List[Path]:
    out: List[Path] = []
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            for f in sorted(p.rglob("*")):
                if f.is_file() and f.suffix.lower() in SUPPORTED and not any(part.startswith(".") or part in ("node_modules", "venv", ".venv", "build", "dist") for part in f.parts):
                    out.append(f)
        elif p.is_file():
            out.append(p)
        else:
            raise FileNotFoundError(raw)
    return out


def _level(name: str) -> Optional[int]:
    n = name.strip().lower()
    if n in ("never", "none", "off"):
        return None
    table = {"retracted": 4, "not-found": 3, "notfound": 3, "mismatch": 3, "dead-link": 2, "deadlink": 2, "likely": 1, "unreachable": 1}
    if n not in table:
        raise ValueError("unknown --fail-on level %r" % name)
    return table[n]


def main(argv: Optional[List[str]] = None, fetch: Optional[sources.Fetcher] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")  # type: ignore[attr-defined]
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    color = use_color(args.color, args.no_color)
    if args.clear_cache:
        print("removed %d cached lookup(s) from %s" % (cache.clear(), cache.cache_dir()))
        return EXIT_OK
    if not args.paths:
        parser.print_help()
        return EXIT_ERROR
    if args.mailto:
        sources.MAILTO = args.mailto
        sources.USER_AGENT = "citetrust/%s (+https://github.com/kosys0224-spec/citetrust; mailto:%s)" % (__version__, args.mailto)
    try:
        fail_level = _level(args.fail_on)
    except ValueError as exc:
        parser.error(str(exc))
        return EXIT_ERROR  # pragma: no cover

    refs = []
    try:
        if args.paths == ["-"]:
            refs = extract_from_text(sys.stdin.read())
        else:
            for f in _collect(args.paths):
                refs.extend(extract_references(f))
    except FileNotFoundError as exc:
        print("citetrust: file not found: %s" % exc, file=sys.stderr)
        return EXIT_ERROR
    except (OSError, ValueError) as exc:
        print("citetrust: cannot read input: %s" % exc, file=sys.stderr)
        return EXIT_ERROR

    if not refs:
        print("citetrust: no references found. Supported: a References/Bibliography section, [n]/numbered entries, "
              "author-year entries, DOIs, arXiv ids, PMIDs, URLs, BibTeX, \\bibitem.", file=sys.stderr)
        return EXIT_OK

    if args.list:
        for r in refs:
            ident = r.doi and ("doi:" + r.doi) or r.arxiv and ("arXiv:" + r.arxiv) or r.pmid and ("PMID:" + r.pmid) or r.url or "-"
            print("%s:%s  [%s]  %s  %s  (%s)" % (os.path.basename(r.source), r.line, r.kind, ident, (r.title or "")[:70], r.year or "?"))
        print("%d reference(s)" % len(refs), file=sys.stderr)
        return EXIT_OK

    fetcher = fetch or sources.http_fetch
    if not args.no_cache and fetch is None:
        fetcher = cache.cached_fetcher(fetcher)

    progress_on = not (args.json or args.markdown or args.sarif) and sys.stderr.isatty()
    done = [0]

    def progress(_res):
        done[0] += 1
        if progress_on:
            sys.stderr.write("\rcitetrust: checking %d/%d ..." % (done[0], len(refs)))
            sys.stderr.flush()

    try:
        results = verify_references(refs, fetcher, workers=args.workers, check_urls=not args.no_urls, progress=progress)
    except KeyboardInterrupt:
        print("\ncitetrust: interrupted", file=sys.stderr)
        return EXIT_ERROR
    if progress_on:
        sys.stderr.write("\r" + " " * 40 + "\r")

    if args.json:
        print(render_json(results))
    elif args.markdown:
        print(render_markdown(results))
    elif args.sarif:
        print(render_sarif(results))
    else:
        print(render_text(results, color=color, verbose=args.verbose, only_problems=args.only_problems))

    if fail_level is not None and any(Status.RANK[r.status] >= fail_level for r in results):
        return EXIT_PROBLEMS
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
