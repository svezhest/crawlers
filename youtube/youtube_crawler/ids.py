"""Turn URLs / bare ids / files of either into clean 11-char video ids."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")


def extract_video_id(s: str) -> str | None:
    s = s.strip()
    if _ID_RE.match(s):
        return s
    p = urlparse(s)
    host = (p.hostname or "").lower()
    if host in ("youtu.be", "www.youtu.be"):
        vid = p.path.lstrip("/").split("/")[0]
        return vid if _ID_RE.match(vid) else None
    if host.endswith("youtube.com"):
        q = parse_qs(p.query)
        if "v" in q and _ID_RE.match(q["v"][0]):
            return q["v"][0]
        parts = p.path.split("/")
        for prefix in ("embed", "v", "shorts", "live"):
            if len(parts) > 2 and parts[1] == prefix and _ID_RE.match(parts[2]):
                return parts[2]
    return None


def collect_ids(items: list[str], files: list[str]) -> list[str]:
    """Unique ids in first-seen order from CLI args and files (one URL/id per line,
    `#` comments and blank lines ignored; a tab-separated first column is fine)."""
    out: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        raw = raw.split("\t")[0].strip()
        if not raw or raw.startswith("#"):
            return
        vid = extract_video_id(raw)
        if vid and vid not in seen:
            seen.add(vid)
            out.append(vid)

    for it in items:
        add(it)
    for f in files:
        for line in Path(f).read_text(encoding="utf-8").splitlines():
            add(line)
    return out
