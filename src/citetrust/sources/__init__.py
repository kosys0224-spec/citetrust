"""HTTP access to public scholarly indexes. Every function takes an optional `fetch` so tests run offline."""
from __future__ import annotations

import gzip
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Callable, Dict, Optional, Tuple
from xml.etree import ElementTree as ET

from .. import __version__
from ..model import Record

MAILTO = os.environ.get("CITETRUST_MAILTO", "")
USER_AGENT = "citetrust/%s (+https://github.com/kosys0224-spec/citetrust%s)" % (__version__, ("; mailto:" + MAILTO) if MAILTO else "")
TIMEOUT = 20.0


class HttpResponse:
    def __init__(self, status: int, body: bytes, url: str, headers: Optional[Dict[str, str]] = None):
        self.status = status
        self.body = body
        self.url = url
        self.headers = headers or {}

    def json(self):
        return json.loads(self.body.decode("utf-8", errors="replace"))

    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


Fetcher = Callable[[str, str], HttpResponse]  # (url, method) -> HttpResponse


class NetworkError(RuntimeError):
    pass


def http_fetch(url: str, method: str = "GET") -> HttpResponse:
    req = urllib.request.Request(url, method=method, headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/html;q=0.8, */*;q=0.5", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:  # noqa: S310
            body = resp.read(2_000_000) if method != "HEAD" else b""
            if resp.headers.get("Content-Encoding") == "gzip" and body:
                try:
                    body = gzip.decompress(body)
                except OSError:
                    pass
            return HttpResponse(resp.status, body, resp.geturl(), dict(resp.headers))
    except urllib.error.HTTPError as exc:
        body = b""
        try:
            body = exc.read(200_000)
        except Exception:
            pass
        return HttpResponse(exc.code, body, exc.geturl() or url, dict(exc.headers or {}))
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        raise NetworkError("%s: %s" % (url.split("/")[2] if "//" in url else url, getattr(exc, "reason", exc))) from exc


def _mailto(url: str) -> str:
    if MAILTO and "mailto=" not in url:
        url += ("&" if "?" in url else "?") + "mailto=" + urllib.parse.quote(MAILTO)
    return url


def _year_from_parts(obj) -> Optional[int]:
    try:
        parts = obj["date-parts"][0]
        return int(parts[0]) if parts and parts[0] else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


# ------------------------------------------------------------------ DOI handle

def doi_exists(doi: str, fetch: Fetcher = http_fetch) -> Optional[bool]:
    """True/False if the DOI handle is registered; None if we could not tell."""
    url = "https://doi.org/api/handles/" + urllib.parse.quote(doi, safe="/")
    r = fetch(url, "GET")
    if r.status == 404:
        return False
    if r.status != 200:
        return None
    try:
        return r.json().get("responseCode") == 1
    except ValueError:
        return None


# ------------------------------------------------------------------ Crossref

def _crossref_record(item: dict) -> Record:
    titles = item.get("title") or []
    authors = []
    for a in item.get("author") or []:
        fam = a.get("family") or a.get("name") or ""
        if fam:
            authors.append(fam)
    retracted = False
    note = ""
    for upd in item.get("update-to") or []:
        if "retract" in str(upd.get("type", "")).lower():
            retracted = True
            note = "Crossref update-to: %s (%s)" % (upd.get("type"), upd.get("DOI", ""))
    if any(str(t).upper().startswith("RETRACTED") for t in titles):
        retracted = True
        note = note or "title starts with RETRACTED"
    for key in ("is-retracted-by", "has-retraction"):
        rel = (item.get("relation") or {}).get(key)
        if rel:
            retracted = True
            note = note or "Crossref relation %s" % key
    container = (item.get("container-title") or [""])[0] if item.get("container-title") else ""
    return Record(
        source="crossref", id=item.get("DOI", ""), title=titles[0] if titles else "", authors=authors,
        year=_year_from_parts(item.get("issued") or {}) or _year_from_parts(item.get("published-print") or {}) or _year_from_parts(item.get("published-online") or {}),
        container=container, doi=item.get("DOI"), url=item.get("URL", ""), retracted=retracted, retraction_note=note,
        extra={"type": item.get("type"), "score": item.get("score"), "publisher": item.get("publisher")},
    )


def crossref_by_doi(doi: str, fetch: Fetcher = http_fetch) -> Optional[Record]:
    r = fetch(_mailto("https://api.crossref.org/works/" + urllib.parse.quote(doi, safe="")), "GET")
    if r.status == 404:
        return None
    if r.status != 200:
        raise NetworkError("Crossref HTTP %d" % r.status)
    return _crossref_record(r.json()["message"])


def crossref_search(query: str, rows: int = 3, fetch: Fetcher = http_fetch):
    q = urllib.parse.quote(" ".join(query.split())[:300])
    url = _mailto("https://api.crossref.org/works?query.bibliographic=%s&rows=%d&select=DOI,title,author,issued,container-title,score,type,URL,update-to,relation" % (q, rows))
    r = fetch(url, "GET")
    if r.status != 200:
        raise NetworkError("Crossref HTTP %d" % r.status)
    return [_crossref_record(i) for i in r.json().get("message", {}).get("items", [])]


# ------------------------------------------------------------------ OpenAlex

def _openalex_record(w: dict) -> Record:
    authors = []
    for a in w.get("authorships") or []:
        name = (a.get("author") or {}).get("display_name") or ""
        if name:
            authors.append(name.split()[-1])
    doi = (w.get("doi") or "").replace("https://doi.org/", "") or None
    container = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
    return Record(source="openalex", id=w.get("id", ""), title=w.get("title") or w.get("display_name") or "", authors=authors,
                  year=w.get("publication_year"), container=container, doi=doi, url=w.get("id", ""),
                  retracted=bool(w.get("is_retracted")), retraction_note="OpenAlex is_retracted" if w.get("is_retracted") else "",
                  extra={"type": w.get("type"), "cited_by": w.get("cited_by_count")})


def openalex_by_doi(doi: str, fetch: Fetcher = http_fetch) -> Optional[Record]:
    r = fetch(_mailto("https://api.openalex.org/works/https://doi.org/" + urllib.parse.quote(doi, safe="/")), "GET")
    if r.status == 404:
        return None
    if r.status != 200:
        raise NetworkError("OpenAlex HTTP %d" % r.status)
    return _openalex_record(r.json())


def openalex_search(title: str, fetch: Fetcher = http_fetch):
    url = _mailto("https://api.openalex.org/works?search=%s&per-page=3" % urllib.parse.quote(title[:300]))
    r = fetch(url, "GET")
    if r.status != 200:
        raise NetworkError("OpenAlex HTTP %d" % r.status)
    return [_openalex_record(w) for w in r.json().get("results", [])]


# ------------------------------------------------------------------ arXiv

_ATOM = "{http://www.w3.org/2005/Atom}"


def _arxiv_records(xml_text: str):
    out = []
    root = ET.fromstring(xml_text)
    for e in root.findall(_ATOM + "entry"):
        ident = (e.findtext(_ATOM + "id") or "").strip()
        aid = re.sub(r"^.*/abs/", "", ident)
        aid = re.sub(r"v\d+$", "", aid)
        title = " ".join((e.findtext(_ATOM + "title") or "").split())
        if not aid or title.lower() == "error":
            continue
        authors = [" ".join((a.findtext(_ATOM + "name") or "").split()).split()[-1] for a in e.findall(_ATOM + "author") if (a.findtext(_ATOM + "name") or "").strip()]
        published = e.findtext(_ATOM + "published") or ""
        year = int(published[:4]) if published[:4].isdigit() else None
        doi_el = e.find("{http://arxiv.org/schemas/atom}doi")
        out.append(Record(source="arxiv", id=aid, title=title, authors=authors, year=year, container="arXiv", doi=doi_el.text.strip() if doi_el is not None and doi_el.text else None, url="https://arxiv.org/abs/" + aid))
    return out


def arxiv_by_id(arxiv_id: str, fetch: Fetcher = http_fetch) -> Optional[Record]:
    r = fetch("https://export.arxiv.org/api/query?id_list=" + urllib.parse.quote(arxiv_id), "GET")
    if r.status != 200:
        raise NetworkError("arXiv HTTP %d" % r.status)
    recs = _arxiv_records(r.text())
    return recs[0] if recs else None


def arxiv_search(title: str, fetch: Fetcher = http_fetch):
    q = urllib.parse.quote('ti:"%s"' % re.sub(r"[^\w\s]", " ", title)[:200])
    r = fetch("https://export.arxiv.org/api/query?search_query=%s&max_results=3" % q, "GET")
    if r.status != 200:
        raise NetworkError("arXiv HTTP %d" % r.status)
    return _arxiv_records(r.text())


# ------------------------------------------------------------------ PubMed

def pubmed_by_id(pmid: str, fetch: Fetcher = http_fetch) -> Optional[Record]:
    r = fetch("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?db=pubmed&retmode=json&id=" + pmid, "GET")
    if r.status != 200:
        raise NetworkError("PubMed HTTP %d" % r.status)
    res = r.json().get("result", {}).get(pmid)
    if not res or res.get("error"):
        return None
    authors = [a.get("name", "").split()[0] for a in res.get("authors", []) if a.get("name")]
    doi = next((i.get("value") for i in res.get("articleids", []) if i.get("idtype") == "doi"), None)
    pubdate = res.get("pubdate", "")
    year = int(pubdate[:4]) if pubdate[:4].isdigit() else None
    retracted = any("Retracted Publication" in t for t in res.get("pubtype", []))
    return Record(source="pubmed", id=pmid, title=res.get("title", "").rstrip("."), authors=authors, year=year,
                  container=res.get("fulljournalname", ""), doi=doi, url="https://pubmed.ncbi.nlm.nih.gov/%s/" % pmid,
                  retracted=retracted, retraction_note="PubMed pubtype Retracted Publication" if retracted else "")


# ------------------------------------------------------------------ URLs

_SOFT404 = re.compile(r"<title>[^<]*(not found|404|page doesn.t exist|no longer available|removed)[^<]*</title>", re.I)


def check_url(url: str, fetch: Fetcher = http_fetch) -> Tuple[str, str]:
    """Return (state, detail): ok | dead | blocked | error."""
    try:
        r = fetch(url, "HEAD")
        if r.status in (405, 403, 501) or r.status >= 500:
            r = fetch(url, "GET")
    except NetworkError as exc:
        return "error", str(exc)
    if r.status in (404, 410):
        return "dead", "HTTP %d" % r.status
    if r.status in (401, 403, 429) or r.status >= 500:
        return "blocked", "HTTP %d (could not verify)" % r.status
    if r.status >= 400:
        return "dead", "HTTP %d" % r.status
    if r.body and _SOFT404.search(r.text()[:4000]):
        return "dead", "page title says not found (soft 404)"
    final = r.url or url
    try:
        if urllib.parse.urlparse(final).path in ("", "/") and urllib.parse.urlparse(url).path not in ("", "/"):
            return "dead", "redirected to the site's home page (%s)" % final
    except ValueError:
        pass
    return "ok", "HTTP %d" % r.status


def wayback_snapshot(url: str, fetch: Fetcher = http_fetch) -> Optional[str]:
    try:
        r = fetch("https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe=""), "GET")
        snap = r.json().get("archived_snapshots", {}).get("closest", {})
        return snap.get("url") if snap.get("available") else None
    except Exception:
        return None
