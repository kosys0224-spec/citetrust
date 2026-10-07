"""Small on-disk cache for index lookups, so re-running on the same document is instant and polite."""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

from .sources import Fetcher, HttpResponse, NetworkError

DEFAULT_TTL = 7 * 24 * 3600


def cache_dir() -> Path:
    env = os.environ.get("CITETRUST_CACHE_DIR")
    if env:
        return Path(env)
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "citetrust"


def cached_fetcher(inner: Fetcher, ttl: int = DEFAULT_TTL) -> Fetcher:
    """Wrap a fetcher so successful (2xx/404) GET responses are reused for `ttl` seconds."""
    d = cache_dir()

    def fetch(url: str, method: str = "GET") -> HttpResponse:
        if method != "GET":
            return inner(url, method)
        key = hashlib.sha256(url.encode("utf-8")).hexdigest()[:32]
        path = d / (key + ".json")
        try:
            if path.exists() and time.time() - path.stat().st_mtime < ttl:
                data = json.loads(path.read_text(encoding="utf-8"))
                return HttpResponse(data["status"], data["body"].encode("utf-8"), data.get("url", url))
        except (OSError, ValueError, KeyError):
            pass
        resp = inner(url, method)
        if resp.status in (200, 404):
            try:
                d.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(".tmp")
                tmp.write_text(json.dumps({"status": resp.status, "url": resp.url, "body": resp.body.decode("utf-8", errors="replace")}), encoding="utf-8")
                os.replace(tmp, path)
            except OSError:
                pass
        return resp

    return fetch


def clear() -> int:
    d = cache_dir()
    n = 0
    if d.exists():
        for f in d.glob("*.json"):
            try:
                f.unlink()
                n += 1
            except OSError:
                pass
    return n
