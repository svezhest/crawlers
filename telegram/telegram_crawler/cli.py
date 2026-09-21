"""crawl-telegram — download Telegram chats and channels to JSONL.

    crawl-telegram fetch @channel --from 2026-08-01
    crawl-telegram fetch "work chat" --pick           # browse your dialogs by name fragment
    crawl-telegram update all                          # append new messages to fetched chats
    crawl-telegram upgrade my-channel --media photos   # backfill attachments
    crawl-telegram reresolve all                       # retry @mention resolution after a FloodWait

Credentials: TG_API_ID / TG_API_HASH in ./.env (see .env.example). First run asks for the
phone + code and stores a Telethon session file (gitignored).
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import typer

from .config import Config
from . import fetch as F

app = typer.Typer(add_completion=False, help=__doc__)
OUT = typer.Option("data", "--out", help="output directory")


@app.command()
def fetch(query: str = typer.Argument("", help="@username, t.me link, name fragment (with --pick), or numeric id"),
          out: str = OUT,
          limit: Optional[int] = typer.Option(None, help="max messages (newest first)"),
          with_photos: bool = typer.Option(False, "--with-photos", help="download photos too"),
          pick: bool = typer.Option(False, "--pick", "-p", help="browse your dialogs matching QUERY"),
          from_date: Optional[str] = typer.Option(None, "--from", help="YYYY-MM-DD"),
          to_date: Optional[str] = typer.Option(None, "--to", help="YYYY-MM-DD"),
          yes: bool = typer.Option(False, "--yes", "-y", help="skip the confirmation prompt"),
          no_mentions: bool = typer.Option(False, "--no-mentions", help="don't resolve @handles (faster)")):
    """Download one chat to <out>/<slug>/ (replaces a previous fetch of the same chat)."""
    asyncio.run(F.fetch_chat(Config.load(), Path(out), query, limit=limit, photos=with_photos, pick=pick,
                             from_date=from_date, to_date=to_date, yes=yes, mentions=not no_mentions))


@app.command()
def update(targets: list[str] = typer.Argument(..., help="'all' or slugs under <out>"),
           out: str = OUT,
           with_photos: bool = typer.Option(False, "--with-photos"),
           from_date: Optional[str] = typer.Option(None, "--from"),
           to_date: Optional[str] = typer.Option(None, "--to"),
           no_mentions: bool = typer.Option(False, "--no-mentions")):
    """Append NEW messages to already-fetched chats."""
    asyncio.run(F.update_chats(Config.load(), Path(out), list(targets), photos=with_photos,
                               from_date=from_date, to_date=to_date, mentions=not no_mentions))


@app.command()
def reresolve(targets: list[str] = typer.Argument(...), out: str = OUT):
    """Re-resolve @mentions left unresolved by a FloodWait."""
    asyncio.run(F.reresolve(Config.load(), Path(out), list(targets)))


@app.command()
def upgrade(folders: list[str] = typer.Argument(...), out: str = OUT,
            media: str = typer.Option(..., "--media", "-m", help="photos | audio | files | all")):
    """Backfill media files left as placeholders."""
    asyncio.run(F.upgrade(Config.load(), Path(out), list(folders), media))


if __name__ == "__main__":
    app()
