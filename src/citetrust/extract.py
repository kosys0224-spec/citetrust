"""Pull references out of Markdown, plain text, reST, LaTeX, HTML, DOCX and BibTeX files."""
from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path
from typing import Iterable, List, Optional, Tuple
from xml.etree import ElementTree as ET

from .model import Reference

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>\]}]+)", re.I)
ARXIV_RE = re.compile(r"(?:arxiv\s*:\s*|arxiv\.org/(?:abs|pdf)/)(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?", re.I)
PMID_RE = re.compile(r"\bPMID\s*:?\s*(\d{5,9})\b", re.I)
URL_RE = re.compile(r"https?://[^\s<>\"')\]\u2026]+", re.I)
YEAR_RE = re.compile(r"\b(1[89]\d{2}|20\d{2})[a-z]?\b")
HEADING_RE = re.compile(r"^\s*(?:#{1,6}\s*|\\(?:section|chapter)\*?\{)?\s*(references?|bibliography|works cited|literature cited|citations?|sources|notes and references|\u53c2\u8003\u6587\u732e|\ucc38\uace0\ubb38\ud5cc|literatur(?:verzeichnis)?|r\u00e9f\u00e9rences|referencias|bibliograf\u00eda)\s*\}?\s*:?\s*$", re.I)
ENTRY_START_RE = re.compile(r"^\s*(?:\[(\d{1,3}|[A-Za-z][\w:+-]{0,30})\]|(\d{1,3})[.)]\s|\u2022\s|-\s|\*\s|\u2013\s)")
_TRAIL = ".,;:)]}>\"'"


def _clean_doi(d: str) -> str:
    d = d.strip()
    while d and (d[-1] in ".,;:]}>\"'*_" or (d[-1] == ")" and d.count(")") > d.count("("))):
        d = d[:-1]
    return d


def _clean_url(u: str) -> str:
    u = u.rstrip(_TRAIL)
    # balance parentheses (Wikipedia-style URLs)
    while u.count("(") < u.count(")") and u.endswith(")"):
        u = u[:-1]
    return u


# --------------------------------------------------------------------------- readers

def read_document(path: Path) -> List[Tuple[int, str]]:
    """Return (line_number, text) pairs for any supported file."""
    suffix = path.suffix.lower()
    if suffix == ".docx":
        return _read_docx(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    if suffix in (".html", ".htm"):
        text = _html_to_text(text)
    return list(enumerate(text.splitlines(), 1))


def _read_docx(path: Path) -> List[Tuple[int, str]]:
    ns = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
    lines: List[Tuple[int, str]] = []
    n = 0
    with zipfile.ZipFile(path) as zf:
        for member in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
            if member not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(member))
            for p in root.iter("{%s}p" % ns["w"]):
                parts = []
                for node in p.iter():
                    if node.tag == "{%s}t" % ns["w"] and node.text:
                        parts.append(node.text)
                    elif node.tag in ("{%s}tab" % ns["w"],):
                        parts.append("\t")
                    elif node.tag == "{%s}instrText" % ns["w"] and node.text and "HYPERLINK" in node.text:
                        m = re.search(r'HYPERLINK\s+"([^"]+)"', node.text)
                        if m:
                            parts.append(" " + m.group(1) + " ")
                n += 1
                lines.append((n, "".join(parts)))
            # hyperlinks stored in relationships
        rels = "word/_rels/document.xml.rels"
        if rels in zf.namelist():
            rel_root = ET.fromstring(zf.read(rels))
            targets = [r.get("Target") for r in rel_root if r.get("TargetMode") == "External" and r.get("Target")]
            for t in targets:
                if t and (DOI_RE.search(t) or t.startswith("http")):
                    n += 1
                    lines.append((n, t))
    return lines


def _html_to_text(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</div>|</h\d>|</tr>", "\n", text)
    text = re.sub(r"<a\s[^>]*href=\"([^\"]+)\"[^>]*>", r" \1 ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return html.unescape(text)


# --------------------------------------------------------------------------- bibtex

_BIB_START_RE = re.compile(r"@(\w+)\s*\{")


def _bib_entries(text: str):
    """Yield (etype, key, body, start_offset) using brace matching (handles one-line and nested entries)."""
    for m in _BIB_START_RE.finditer(text):
        depth = 1
        i = m.end()
        while i < len(text) and depth:
            c = text[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
            i += 1
        inner = text[m.end(): i - 1]
        etype = m.group(1).lower()
        if "," in inner:
            key, body = inner.split(",", 1)
        else:
            key, body = inner, ""
        yield etype, key.strip(), body, m.start(), text[m.start(): i]
_BIB_FIELD_RE = re.compile(r"(\w+)\s*=\s*(\{(?:[^{}]|\{[^{}]*\})*\}|\"[^\"]*\"|[^,\n]+)\s*,?", re.S)


def _strip_braces(v: str) -> str:
    v = v.strip().rstrip(",").strip()
    while v.startswith("{") and v.endswith("}"):
        v = v[1:-1]
    if v.startswith('"') and v.endswith('"'):
        v = v[1:-1]
    v = re.sub(r"[{}]", "", v)
    v = re.sub(r"\\['`^\"~=.]", "", v)
    return " ".join(v.split())


def extract_bibtex(text: str, source: str = "") -> List[Reference]:
    refs = []
    for etype, key, body, start, whole in _bib_entries(text):
        if etype in ("comment", "preamble", "string") or not key:
            continue
        fields = {}
        for fm in _BIB_FIELD_RE.finditer(body):
            fields[fm.group(1).lower()] = _strip_braces(fm.group(2))
        line = text.count("\n", 0, start) + 1
        ref = Reference(raw=whole.strip(), source=source, line=line, kind="bibtex", key=key)
        ref.title = fields.get("title") or None
        ref.year = _year(fields.get("year", ""))
        ref.container = fields.get("journal") or fields.get("booktitle") or None
        if fields.get("author"):
            ref.authors = [_family(a) for a in re.split(r"\s+and\s+", fields["author"]) if a.strip()][:8]
        doi = fields.get("doi")
        if doi:
            ref.doi = _clean_doi(re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I))
        eprint = fields.get("eprint") or ""
        if eprint and re.match(r"^\d{4}\.\d{4,5}", eprint):
            ref.arxiv = eprint
        am = ARXIV_RE.search(" ".join(fields.values()))
        if am and not ref.arxiv:
            ref.arxiv = am.group(1)
        url = fields.get("url")
        if url and not ref.doi and not ref.arxiv:
            ref.url = _clean_url(url)
        refs.append(ref)
    return refs


def _family(author: str) -> str:
    a = author.strip()
    if "," in a:
        return a.split(",", 1)[0].strip()
    parts = a.split()
    return parts[-1] if parts else a


def _year(s: str) -> Optional[int]:
    m = YEAR_RE.search(s or "")
    return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- free text

def _parse_entry(text: str) -> Tuple[Optional[str], List[str], Optional[int], Optional[str]]:
    """Best-effort split of a bibliography entry into (title, [family names], year, container)."""
    t = " ".join(text.split())
    t = re.sub(r"^\s*(?:\[[^\]]{1,32}\]|\d{1,3}[.)])\s*", "", t)
    # drop identifiers/urls so they do not pollute title detection
    t_wo = DOI_RE.sub(" ", URL_RE.sub(" ", t))
    t_wo = re.sub(r"(?i)\b(doi|arxiv|pmid)\s*:\s*\S+", " ", t_wo)
    t_wo = re.sub(r"\s+", " ", t_wo).strip()
    year = _year(t_wo)
    container = None
    # Markdown emphasis usually marks the journal/book (APA) - remember it, then strip the markers.
    em = re.search(r"(?<![*_\w])[*_]{1,2}([^*_]{3,120}?)[*_]{1,2}(?![\w])", t_wo)
    if em:
        container = em.group(1).strip().rstrip(",.")
    plain = re.sub(r"[*_]{1,2}([^*_]{1,160}?)[*_]{1,2}", r"\1", t_wo)
    title = None
    qm = re.search(r"[\u201c\"]([^\u201d\"]{8,})[\u201d\"]", plain)
    if qm:
        title = qm.group(1).strip().rstrip(".,")
    # author-year style: Authors (2020). Title. Venue.
    m = re.match(r"^(.*?)\(?\b(1[89]\d{2}|20\d{2})[a-z]?\)?[.:,]?\s+(.+)$", plain)
    authors_part, rest = ("", plain)
    if m and len(m.group(1)) < 200:
        authors_part, rest = m.group(1), m.group(3)
    else:
        parts = plain.split(". ")
        if len(parts) >= 2:
            authors_part, rest = parts[0], ". ".join(parts[1:])
    if not title:
        segs = [s.strip() for s in re.split(r"(?<=[a-z0-9\)\]?!])\.\s+|\.\s+(?=[A-Z])", rest) if s.strip()]
        for s in segs:
            s_clean = s.rstrip(".,;")
            if re.match(r"^(in|proc\.?|proceedings|vol\.?|pp\.?|doi|retrieved|accessed|available|https?)\b", s_clean, re.I):
                continue
            if container and s_clean.lower().startswith(container.lower()):
                continue
            if len(s_clean) >= 8:
                title = s_clean
                break
    if not container and title:
        after = rest.split(title, 1)[1] if title in rest else ""
        cm = re.match(r"^[.,:\s]*(?:In:?\s*)?([A-Z][^.,;(]{3,80})", after)
        if cm:
            container = cm.group(1).strip()
    authors = []
    ap = re.sub(r"\b(and|&|et al\.?)\b", ",", authors_part, flags=re.I)
    for chunk in re.split(r"[;,]", ap):
        chunk = chunk.strip(" .")
        if not chunk or len(chunk) < 2:
            continue
        words = [w for w in re.split(r"\s+", chunk) if w]
        caps = [w for w in words if re.match(r"^[A-Z][a-zA-Z'\-]{1,}$", w) and not re.match(r"^[A-Z]\.?$", w)]
        if caps:
            authors.append(caps[-1] if re.match(r"^[A-Z]\.", words[0]) else caps[0])
        if len(authors) >= 6:
            break
    if title and len(title) > 200:
        title = title[:200]
    return title, authors, year, container


def _looks_like_entry(line: str, in_section: bool = False) -> bool:
    s = line.strip()
    if len(s) < 25:
        return False
    if ENTRY_START_RE.match(s):
        return True
    if YEAR_RE.search(s) and re.search(r"[A-Z][a-z]+,?\s+(?:[A-Z]\.|and|&)", s):
        return True
    if in_section and (DOI_RE.search(s) or ARXIV_RE.search(s)):
        return True
    return False


def extract_text_references(lines: Iterable[Tuple[int, str]], source: str = "") -> List[Reference]:
    lines = list(lines)
    refs: List[Reference] = []
    seen = set()
    in_bib = False
    # 1) bibliography section (if present) - entries may wrap over several lines
    start = None
    for i, (_, text) in enumerate(lines):
        if HEADING_RE.match(text.strip().strip("*_")):
            start = i + 1
            break
    entries: List[Tuple[int, str]] = []
    if start is not None:
        in_bib = True
        buf: List[str] = []
        buf_line = 0
        for ln, text in lines[start:]:
            s = text.strip()
            if re.match(r"^\s*#{1,6}\s+\S", text) or (s and HEADING_RE.match(s)):
                break  # next heading
            if not s:
                if buf:
                    entries.append((buf_line, " ".join(buf)))
                    buf = []
                continue
            if ENTRY_START_RE.match(s) and buf:
                entries.append((buf_line, " ".join(buf)))
                buf = []
            if not buf:
                buf_line = ln
            buf.append(s)
        if buf:
            entries.append((buf_line, " ".join(buf)))
    else:
        # 2) no heading: any line that looks like an entry
        for ln, text in lines:
            if _looks_like_entry(text):
                entries.append((ln, text.strip()))
    for ln, entry in entries:
        if not _looks_like_entry(entry, in_section=in_bib) and not (in_bib and URL_RE.search(entry)):
            continue
        ref = Reference(raw=entry, source=source, line=ln, kind="text")
        km = re.match(r"^\s*\[([^\]]{1,32})\]", entry)
        if km:
            ref.key = km.group(1)
        dm = DOI_RE.search(entry)
        if dm:
            ref.doi = _clean_doi(dm.group(1))
        am = ARXIV_RE.search(entry)
        if am:
            ref.arxiv = am.group(1)
        pm = PMID_RE.search(entry)
        if pm:
            ref.pmid = pm.group(1)
        um = [u for u in URL_RE.findall(entry) if not re.search(r"doi\.org|arxiv\.org", u, re.I)]
        if um:
            ref.url = _clean_url(um[0])
        ref.title, ref.authors, ref.year, ref.container = _parse_entry(entry)
        sig = re.sub(r"\W+", "", entry.lower())[:160]
        if sig in seen:
            continue
        seen.add(sig)
        refs.append(ref)
    # 3) identifiers mentioned inline anywhere else (DOIs, arXiv, PMIDs, bare URLs) not already captured
    captured_dois = {r.doi for r in refs if r.doi}
    captured_arxiv = {r.arxiv for r in refs if r.arxiv}
    captured_urls = {r.url for r in refs if r.url}
    for ln, text in lines:
        for m in DOI_RE.finditer(text):
            d = _clean_doi(m.group(1))
            if d not in captured_dois:
                captured_dois.add(d)
                refs.append(Reference(raw=text.strip(), source=source, line=ln, kind="doi", doi=d))
        for m in ARXIV_RE.finditer(text):
            a = m.group(1)
            if a not in captured_arxiv:
                captured_arxiv.add(a)
                refs.append(Reference(raw=text.strip(), source=source, line=ln, kind="arxiv", arxiv=a))
        for m in PMID_RE.finditer(text):
            if not any(r.pmid == m.group(1) for r in refs):
                refs.append(Reference(raw=text.strip(), source=source, line=ln, kind="pmid", pmid=m.group(1)))
        if not in_bib or ln < (lines[start][0] if start is not None and start < len(lines) else 10**9):
            for u in URL_RE.findall(text):
                u = _clean_url(u)
                if re.search(r"doi\.org|arxiv\.org", u, re.I) or u in captured_urls or "." not in u.split("//", 1)[-1].split("/")[0]:
                    continue
                captured_urls.add(u)
                refs.append(Reference(raw=text.strip(), source=source, line=ln, kind="url", url=u))
    return refs


def extract_latex_bibitems(text: str, source: str = "") -> List[Reference]:
    refs = []
    for m in re.finditer(r"\\bibitem(?:\[[^\]]*\])?\{([^}]+)\}(.*?)(?=\\bibitem|\\end\{thebibliography\}|$)", text, re.S):
        key, body = m.group(1), " ".join(m.group(2).split())
        body = re.sub(r"\\(?:newblock|emph|textit|textbf|url)\s*\{?", " ", body).replace("}", " ")
        ln = text.count("\n", 0, m.start()) + 1
        ref = Reference(raw=body.strip(), source=source, line=ln, kind="text", key=key)
        dm = DOI_RE.search(body)
        if dm:
            ref.doi = _clean_doi(dm.group(1))
        am = ARXIV_RE.search(body)
        if am:
            ref.arxiv = am.group(1)
        um = [u for u in URL_RE.findall(body) if not re.search(r"doi\.org|arxiv\.org", u, re.I)]
        if um:
            ref.url = _clean_url(um[0])
        ref.title, ref.authors, ref.year, ref.container = _parse_entry(body)
        refs.append(ref)
    return refs


def extract_references(path) -> List[Reference]:
    """Extract references from a file of any supported type."""
    p = Path(path)
    suffix = p.suffix.lower()
    src = str(p)
    if suffix == ".bib":
        return extract_bibtex(p.read_text(encoding="utf-8", errors="replace"), src)
    if suffix == ".tex":
        text = p.read_text(encoding="utf-8", errors="replace")
        refs = extract_latex_bibitems(text, src)
        if refs:
            return refs
        return extract_text_references(enumerate(text.splitlines(), 1), src)
    return extract_text_references(read_document(p), src)


def extract_from_text(text: str, source: str = "<stdin>") -> List[Reference]:
    if text.lstrip().startswith("@") and "@" in text and "{" in text:
        refs = extract_bibtex(text, source)
        if refs:
            return refs
    return extract_text_references(enumerate(text.splitlines(), 1), source)
