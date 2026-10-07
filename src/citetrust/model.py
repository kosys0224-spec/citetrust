"""Data types shared by extraction, verification and reporting."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional


class Status:
    VERIFIED = "verified"        # found and metadata agrees
    LIKELY = "likely"            # found something close; worth a human look
    MISMATCH = "mismatch"        # identifier exists but title/author/year disagree
    NOT_FOUND = "not-found"      # no record anywhere we looked
    RETRACTED = "retracted"      # record exists and is flagged retracted
    DEAD_LINK = "dead-link"      # URL returns 404/410 or similar
    UNREACHABLE = "unreachable"  # network error / 403 / timeout: unknown
    SKIPPED = "skipped"          # nothing checkable (e.g. a bare year)

    ALL = (VERIFIED, LIKELY, MISMATCH, NOT_FOUND, RETRACTED, DEAD_LINK, UNREACHABLE, SKIPPED)
    # severity for --fail-on
    RANK = {RETRACTED: 4, NOT_FOUND: 3, MISMATCH: 3, DEAD_LINK: 2, LIKELY: 1, UNREACHABLE: 1, SKIPPED: 0, VERIFIED: 0}
    BAD = (RETRACTED, NOT_FOUND, MISMATCH, DEAD_LINK)


@dataclass
class Reference:
    """One thing a document cites. Identifiers are optional; free-text fields are what the document *claims*."""
    raw: str                             # the entry text as found
    source: str = ""                     # file path
    line: Optional[int] = None
    kind: str = "text"                   # text | bibtex | doi | arxiv | url | pmid
    key: str = ""                        # bibtex key / [n] label
    doi: Optional[str] = None
    arxiv: Optional[str] = None
    pmid: Optional[str] = None
    url: Optional[str] = None
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)   # family names as claimed
    year: Optional[int] = None
    container: Optional[str] = None      # journal / venue as claimed

    def label(self) -> str:
        if self.key:
            return self.key
        if self.doi:
            return "doi:" + self.doi
        if self.arxiv:
            return "arXiv:" + self.arxiv
        if self.pmid:
            return "PMID:" + self.pmid
        if self.url:
            return self.url[:60]
        return (self.title or self.raw)[:60]

    def has_identifier(self) -> bool:
        return bool(self.doi or self.arxiv or self.pmid or self.url)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Record:
    """What an index (Crossref, OpenAlex, arXiv, PubMed) says exists."""
    source: str
    id: str
    title: str = ""
    authors: List[str] = field(default_factory=list)
    year: Optional[int] = None
    container: str = ""
    doi: Optional[str] = None
    url: str = ""
    retracted: bool = False
    retraction_note: str = ""
    extra: Dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Result:
    ref: Reference
    status: str
    reasons: List[str] = field(default_factory=list)      # human-readable findings
    record: Optional[Record] = None                        # best matching record
    score: Optional[float] = None                          # title similarity 0..1 when computed
    suggestions: List[str] = field(default_factory=list)   # e.g. "DOI: 10.…", "Wayback: …"
    checks: List[str] = field(default_factory=list)        # which lookups ran

    def to_dict(self) -> dict:
        return {
            "reference": self.ref.to_dict(),
            "status": self.status,
            "reasons": self.reasons,
            "record": self.record.to_dict() if self.record else None,
            "score": self.score,
            "suggestions": self.suggestions,
            "checks": self.checks,
        }
