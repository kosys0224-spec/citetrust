# Changelog

All notable changes to citetrust are documented here ([Keep a Changelog](https://keepachangelog.com/en/1.1.0/), [SemVer](https://semver.org/)).

## [0.1.0] - 2026-10-07

Initial public release.

### Added
- Reference extraction from Markdown, plain text, reST, LaTeX (`\bibitem`), HTML, DOCX (body, footnotes, endnotes, hyperlinks) and BibTeX; bibliography-section detection in several languages; inline DOI / arXiv / PMID / URL detection.
- Verification against doi.org, Crossref, OpenAlex, arXiv and PubMed; title/author/year agreement check; retraction flags; URL liveness with soft-404 detection and Wayback Machine suggestions; DOI suggestions for free-text entries.
- Text, `--json`, `--markdown` and `--sarif` reports; `--only-problems`, `--list`, `--fail-on` exit codes; directory input; stdin input.
- 7-day on-disk lookup cache; `--mailto` polite-pool support; parallel lookups.
- 18 offline tests with recorded index responses (`tests/replay.py`).

[0.1.0]: https://github.com/kosys0224-spec/citetrust/releases/tag/v0.1.0
