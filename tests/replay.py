"""A fetcher that answers from recorded fixtures instead of the network (used by tests and the demo)."""
from __future__ import annotations

import re
import urllib.parse
from pathlib import Path

from citetrust.sources import HttpResponse, NetworkError

FIX = Path(__file__).resolve().parent / "fixtures"


def _f(name: str) -> bytes:
    return (FIX / name).read_bytes()


def replay_fetch(url: str, method: str = "GET") -> HttpResponse:
    u = urllib.parse.unquote(url)
    low = u.lower()
    # --- doi.org handles
    if "doi.org/api/handles/" in low:
        if "10.1109/5.771073" in low or "10.1016/s0140-6736(97)11096-0" in low or "10.5281/zenodo.1234567" in low:
            return HttpResponse(200, _f("handle_ok.json"), url)
        return HttpResponse(404, _f("handle_404.json"), url)
    # --- crossref
    if "api.crossref.org/works/" in low:
        if "10.1109/5.771073" in low:
            return HttpResponse(200, _f("crossref_work_paskin.json"), url)
        if "10.1016/s0140-6736(97)11096-0" in low:
            return HttpResponse(200, _f("crossref_work_retracted.json"), url)
        return HttpResponse(404, b'{"status":"error"}', url)
    if "api.crossref.org/works?" in low:
        if "voltage sag" in low or "kim" in low and "rural" in low:
            return HttpResponse(200, _f("crossref_search_kim.json"), url)
        if "transformer-based load" in low or "schmidt" in low:
            return HttpResponse(200, _f("crossref_search_schmidt.json"), url)
        if "grid frequency forecasting" in low or "smith" in low:
            return HttpResponse(200, _f("crossref_search_smith.json"), url)
        return HttpResponse(200, b'{"status":"ok","message":{"items":[]}}', url)
    # --- openalex
    if "api.openalex.org/works/https://doi.org/" in low:
        if "zenodo" in low:
            return HttpResponse(200, _f("openalex_work_zenodo.json"), url)
        return HttpResponse(404, b'{"error":"not found"}', url)
    if "api.openalex.org/works?" in low:
        if "voltage sag" in low:
            return HttpResponse(200, _f("openalex_search_kim.json"), url)
        return HttpResponse(200, _f("openalex_search_empty.json"), url)
    # --- arxiv
    if "export.arxiv.org/api/query?id_list=" in low:
        if "1706.03762" in low:
            return HttpResponse(200, _f("arxiv_1706.03762.xml"), url)
        return HttpResponse(200, _f("arxiv_error.xml"), url)
    if "export.arxiv.org/api/query?search_query" in low:
        return HttpResponse(200, _f("arxiv_empty.xml"), url)
    # --- pubmed
    if "eutils.ncbi.nlm.nih.gov" in low:
        if "31452104" in low:
            return HttpResponse(200, _f("pubmed_31452104.json"), url)
        return HttpResponse(200, b'{"result":{"uids":["1"],"1":{"error":"cannot get document summary"}}}', url)
    # --- wayback
    if "archive.org/wayback/available" in low:
        return HttpResponse(200, _f("wayback_ok.json"), url)
    # --- plain URLs
    if "example.org/entsoe" in low:
        return HttpResponse(404, b"<html><title>Not Found</title></html>", url)
    if "example.org/alive" in low or "example.com/" in low:
        return HttpResponse(200, b"<html><title>Fine</title></html>", url)
    if "unreachable.invalid" in low:
        raise NetworkError("unreachable.invalid: name or service not known")
    if "forbidden.example" in low:
        return HttpResponse(403, b"", url)
    if re.match(r"https?://", low):
        return HttpResponse(200, b"<html><title>ok</title></html>", url)
    raise NetworkError("no fixture for " + url)
