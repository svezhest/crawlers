"""Pacing knobs and env loading. Targets (subreddits, queries) come from the CLI."""
from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("'\""))


load_dotenv(Path.cwd() / ".env")

REQUEST_DELAY = float(os.environ.get("REDDIT_DELAY", "2.5"))   # reddit.com itself
JITTER = 1.0
ARCHIVE_DELAY = float(os.environ.get("ARCHIVE_DELAY", "1.2"))  # Arctic Shift mirror
MAX_RETRIES = 5
BACKOFF_BASE = 4.0
MAX_PAGES_PER_LISTING = 30
LISTING_PAGE_SIZE = 100
# Reddit bans generic User-Agents; make yours unique via REDDIT_USER_AGENT.
USER_AGENT = os.environ.get("REDDIT_USER_AGENT", "script:crawlers-reddit:0.1 (contact: set REDDIT_USER_AGENT)")
LISTINGS: list[tuple[str, dict]] = [("new", {}), ("top", {"t": "all"}), ("hot", {})]
