# Security policy

citetrust sends HTTPS GET/HEAD requests to doi.org, api.crossref.org, api.openalex.org, export.arxiv.org, eutils.ncbi.nlm.nih.gov, archive.org, and to any URL cited in the document you check. It never executes content it downloads. If a document contains URLs you would not want requested from your machine, run with `--no-urls`.

Report security issues (crafted DOCX/BibTeX/HTML that crashes or hangs the parser, path issues in the cache directory, anything that could make the tool do more than read files and perform GET/HEAD requests) through GitHub's private vulnerability reporting on this repository's Security tab. Acknowledgement within 7 days; only the latest release receives fixes.
