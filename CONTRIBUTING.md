# Contributing to citetrust

## Most useful contributions

1. **Entries the parser gets wrong.** Open an issue with the reference text (redacted if needed), what `citetrust --list` extracted, and what it should have been. Citation styles are endless; each fixed case is a test in `tests/test_citetrust.py`.
2. **Recorded index responses** for corner cases (DataCite DOIs, retractions expressed differently, arXiv versions, PubMed Central ids).
3. **New input formats** (`extract.py`) and **new indexes** (`sources/__init__.py`).

## Layout

```
src/citetrust/extract.py      readers (md/txt/rst/tex/html/docx/bib) + entry parsing heuristics
src/citetrust/sources/        doi.org, Crossref, OpenAlex, arXiv, PubMed, URL/Wayback - all take a `fetch` callable
src/citetrust/verify.py       decision logic: existence -> agreement -> retraction; statuses
src/citetrust/report.py       text / json / markdown / sarif
src/citetrust/cache.py        on-disk cache wrapper around any fetcher
src/citetrust/cli.py
tests/replay.py               offline fetcher answering from tests/fixtures/
tests/test_citetrust.py
examples/sample.md            the document in the README screenshot
```

## Rules

- Standard library only. Python 3.9+.
- Every network call goes through the injected `fetch(url, method)` so tests stay offline. Add a fixture under `tests/fixtures/` and a branch in `tests/replay.py` rather than hitting the network in tests.
- Be polite to the indexes: honour `--mailto`, keep request counts per reference small, cache.
- A new status or a changed threshold needs a test and a line in README "What it checks".

## Running

```bash
pip install -e .
python -m unittest discover -s tests -v
citetrust examples/sample.md --list
python scripts/make_screenshot.py      # regenerates docs/demo.png (needs Playwright)
```
