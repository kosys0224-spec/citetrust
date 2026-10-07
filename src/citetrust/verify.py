"""Decide, for each reference, whether it exists, matches what the document claims, and is not retracted."""
from __future__ import annotations

import difflib
import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, List, Optional, Sequence

from . import sources
from .model import Record, Reference, Result, Status

TITLE_OK = 0.85       # similarity at/above which titles are considered the same
TITLE_MAYBE = 0.60    # between MAYBE and OK -> "likely"


def norm_text(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"<[^>]+>", " ", s)
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    s = re.sub(r"\b(the|a|an|of|on|in|for|and|to|with|by|at|from|via|towards|toward)\b", " ", s)
    return " ".join(s.split())


def title_similarity(a: Optional[str], b: Optional[str]) -> Optional[float]:
    if not a or not b:
        return None
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return None
    if na == nb or na in nb or nb in na:
        return 1.0 if len(min(na, nb, key=len)) >= 0.6 * len(max(na, nb, key=len)) else 0.9
    return difflib.SequenceMatcher(None, na, nb).ratio()


def _family_norm(s: str) -> str:
    return norm_text(s).replace(" ", "")


def compare(ref: Reference, rec: Record) -> tuple:
    """Return (ok: bool|None, reasons: list, score: float|None). ok=None when nothing comparable."""
    reasons: List[str] = []
    score = title_similarity(ref.title, rec.title)
    comparable = False
    ok = True
    if score is not None:
        comparable = True
        if score < TITLE_MAYBE:
            ok = False
            reasons.append("title differs: document says “%s”, index has “%s”" % (_short(ref.title), _short(rec.title)))
        elif score < TITLE_OK:
            reasons.append("title only roughly matches (%.0f%%): “%s” vs “%s”" % (score * 100, _short(ref.title), _short(rec.title)))
    if ref.year and rec.year:
        comparable = True
        if abs(ref.year - rec.year) > 1:
            ok = False
            reasons.append("year differs: document says %d, index has %d" % (ref.year, rec.year))
        elif ref.year != rec.year:
            reasons.append("year off by one: %d vs %d (print/online difference?)" % (ref.year, rec.year))
    if ref.authors and rec.authors:
        comparable = True
        doc_first = _family_norm(ref.authors[0])
        rec_firsts = [_family_norm(a) for a in rec.authors[:3]]
        if doc_first and not any(doc_first == r or (len(doc_first) > 3 and (doc_first in r or r in doc_first)) for r in rec_firsts):
            # maybe the document lists authors in a different order; check anywhere in the record
            any_match = any(_family_norm(a) in [_family_norm(r) for r in rec.authors] for a in ref.authors[:3])
            if any_match:
                reasons.append("first author differs (%s vs %s) but a listed author matches" % (ref.authors[0], rec.authors[0] if rec.authors else "?"))
            else:
                ok = False
                reasons.append("authors differ: document says %s, index has %s" % (", ".join(ref.authors[:3]), ", ".join(rec.authors[:3]) or "?"))
    if not comparable:
        return None, reasons, score
    return ok, reasons, score


def _short(s: Optional[str], n: int = 70) -> str:
    s = " ".join((s or "").split())
    return s if len(s) <= n else s[: n - 1] + "…"


def _record_line(rec: Record) -> str:
    return "%s%s%s" % (_short(rec.title, 80), " (%s)" % rec.year if rec.year else "", " — " + rec.source)


def verify_one(ref: Reference, fetch: sources.Fetcher = sources.http_fetch, check_urls: bool = True) -> Result:
    res = Result(ref=ref, status=Status.SKIPPED)
    try:
        if ref.doi:
            return _verify_doi(ref, res, fetch)
        if ref.arxiv:
            return _verify_arxiv(ref, res, fetch)
        if ref.pmid:
            return _verify_pmid(ref, res, fetch)
        if ref.title and len(ref.title) >= 8:
            return _verify_text(ref, res, fetch)
        if ref.url:
            if not check_urls:
                res.status = Status.SKIPPED
                res.reasons.append("URL check disabled")
                return res
            return _verify_url(ref, res, fetch)
        res.reasons.append("nothing checkable (no identifier and no recognisable title)")
        return res
    except sources.NetworkError as exc:
        res.status = Status.UNREACHABLE
        res.reasons.append("network: %s" % exc)
        return res


def _apply_record(ref: Reference, res: Result, rec: Record, found_by: str) -> Result:
    res.record = rec
    res.checks.append(found_by)
    if rec.retracted:
        res.status = Status.RETRACTED
        res.reasons.insert(0, "RETRACTED: %s" % (rec.retraction_note or "flagged by index"))
        return res
    ok, reasons, score = compare(ref, rec)
    res.score = score
    res.reasons.extend(reasons)
    if ok is None:
        res.status = Status.VERIFIED
        res.reasons.append("exists: %s" % _record_line(rec))
    elif ok:
        res.status = Status.VERIFIED if not reasons else Status.LIKELY
        if not reasons:
            res.reasons.append("matches: %s" % _record_line(rec))
    else:
        res.status = Status.MISMATCH
    return res


def _verify_doi(ref: Reference, res: Result, fetch) -> Result:
    res.checks.append("doi.org")
    exists = sources.doi_exists(ref.doi, fetch)
    if exists is False:
        res.status = Status.NOT_FOUND
        res.reasons.append("DOI %s is not registered at doi.org (fabricated or mistyped)" % ref.doi)
        if ref.title:
            _suggest_by_title(ref, res, fetch)
        return res
    rec = None
    try:
        rec = sources.crossref_by_doi(ref.doi, fetch)
        res.checks.append("crossref")
    except sources.NetworkError as exc:
        res.reasons.append("crossref: %s" % exc)
    if rec is None:
        try:
            rec = sources.openalex_by_doi(ref.doi, fetch)
            res.checks.append("openalex")
        except sources.NetworkError as exc:
            res.reasons.append("openalex: %s" % exc)
    if rec is None:
        if exists:
            res.status = Status.LIKELY
            res.reasons.append("DOI resolves, but no metadata in Crossref/OpenAlex (DataCite or unusual registrant?) — check by hand")
        else:
            res.status = Status.UNREACHABLE
            res.reasons.append("could not confirm the DOI with any index")
        return res
    return _apply_record(ref, res, rec, "doi lookup")


def _verify_arxiv(ref: Reference, res: Result, fetch) -> Result:
    res.checks.append("arxiv")
    rec = sources.arxiv_by_id(ref.arxiv, fetch)
    if rec is None:
        res.status = Status.NOT_FOUND
        res.reasons.append("arXiv:%s does not exist" % ref.arxiv)
        if ref.title:
            _suggest_by_title(ref, res, fetch)
        return res
    return _apply_record(ref, res, rec, "arxiv id")


def _verify_pmid(ref: Reference, res: Result, fetch) -> Result:
    res.checks.append("pubmed")
    rec = sources.pubmed_by_id(ref.pmid, fetch)
    if rec is None:
        res.status = Status.NOT_FOUND
        res.reasons.append("PMID %s does not exist" % ref.pmid)
        return res
    return _apply_record(ref, res, rec, "pmid")


def _best(ref: Reference, candidates: Sequence[Record]):
    best, best_score = None, -1.0
    for c in candidates:
        s = title_similarity(ref.title, c.title) or 0.0
        if ref.year and c.year and abs(ref.year - c.year) <= 1:
            s += 0.05
        if ref.authors and c.authors and _family_norm(ref.authors[0]) and any(_family_norm(ref.authors[0]) == _family_norm(a) for a in c.authors[:3]):
            s += 0.05
        if s > best_score:
            best, best_score = c, s
    return best, min(best_score, 1.0)


def _verify_text(ref: Reference, res: Result, fetch) -> Result:
    query = ref.raw if len(ref.raw) < 300 else (ref.title or ref.raw[:300])
    candidates: List[Record] = []
    for name, fn in (("crossref search", lambda: sources.crossref_search(query, 3, fetch)),
                     ("openalex search", lambda: sources.openalex_search(ref.title or query, fetch)),
                     ("arxiv search", lambda: sources.arxiv_search(ref.title or query, fetch))):
        try:
            got = fn()
            res.checks.append(name)
            candidates.extend(got)
            best, score = _best(ref, candidates)
            if best is not None and score >= TITLE_OK:
                break
        except sources.NetworkError as exc:
            res.reasons.append("%s: %s" % (name, exc))
    best, score = _best(ref, candidates)
    if best is None:
        if ref.url:
            res.reasons.append("no record in Crossref/OpenAlex/arXiv; checking the URL instead")
            return _verify_url(ref, res, fetch)
        res.status = Status.NOT_FOUND if not any("network" in r or ": " in r and "HTTP" in r for r in res.reasons) else Status.UNREACHABLE
        res.reasons.append("no record found for “%s” in Crossref, OpenAlex or arXiv" % _short(ref.title or ref.raw))
        return res
    res.score = score
    if best.retracted:
        res.record = best
        res.status = Status.RETRACTED
        res.reasons.insert(0, "RETRACTED: %s" % (best.retraction_note or "flagged by index"))
        return res
    if score >= TITLE_OK:
        ok, reasons, _ = compare(ref, best)
        res.record = best
        res.reasons.extend(reasons)
        res.status = Status.MISMATCH if ok is False else (Status.VERIFIED if not reasons else Status.LIKELY)
        if res.status == Status.VERIFIED:
            res.reasons.append("matches: %s" % _record_line(best))
        if best.doi and not ref.doi:
            res.suggestions.append("DOI: https://doi.org/%s" % best.doi)
        return res
    if score >= TITLE_MAYBE:
        res.record = best
        res.status = Status.LIKELY
        res.reasons.append("closest record (%.0f%% title match): %s" % (score * 100, _record_line(best)))
        if best.doi:
            res.suggestions.append("if this is it: https://doi.org/%s" % best.doi)
        return res
    res.status = Status.NOT_FOUND
    res.reasons.append("no record found for “%s”; closest was “%s” (%.0f%%)" % (_short(ref.title or ref.raw), _short(best.title), score * 100))
    if ref.url:
        res.reasons.append("has a URL; checking it")
        state, detail = sources.check_url(ref.url, fetch)
        res.checks.append("url")
        res.reasons.append("URL %s: %s" % (state, detail))
    return res


def _suggest_by_title(ref: Reference, res: Result, fetch) -> None:
    try:
        cands = sources.crossref_search(ref.title or ref.raw, 2, fetch)
        res.checks.append("crossref search")
        best, score = _best(ref, cands)
        if best is not None and score >= TITLE_OK and best.doi:
            res.suggestions.append("a real record with this title exists: https://doi.org/%s (%s)" % (best.doi, best.year or "?"))
    except sources.NetworkError:
        pass


def _verify_url(ref: Reference, res: Result, fetch) -> Result:
    res.checks.append("url")
    state, detail = sources.check_url(ref.url, fetch)
    if state == "ok":
        res.status = Status.VERIFIED
        res.reasons.append("URL reachable (%s)" % detail)
    elif state == "dead":
        res.status = Status.DEAD_LINK
        res.reasons.append("URL dead: %s" % detail)
        snap = sources.wayback_snapshot(ref.url, fetch)
        if snap:
            res.suggestions.append("Wayback Machine copy: %s" % snap)
    elif state == "blocked":
        res.status = Status.UNREACHABLE
        res.reasons.append("URL could not be verified: %s" % detail)
    else:
        res.status = Status.UNREACHABLE
        res.reasons.append("URL error: %s" % detail)
    return res


def verify_references(refs: List[Reference], fetch: sources.Fetcher = sources.http_fetch, workers: int = 6,
                      check_urls: bool = True, progress: Optional[Callable[[Result], None]] = None) -> List[Result]:
    results: List[Optional[Result]] = [None] * len(refs)
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {pool.submit(verify_one, r, fetch, check_urls): i for i, r in enumerate(refs)}
        for fut, i in futures.items():
            results[i] = fut.result()
            if progress:
                progress(results[i])
    return [r for r in results if r is not None]
