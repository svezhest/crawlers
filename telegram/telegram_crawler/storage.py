"""On-disk layout: one directory per chat.

    <out>/<chat-slug>/
        meta.json          ChatMeta
        messages.jsonl     one Message per line, oldest-first after a full fetch
        media/             downloaded attachments (only when requested)
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, dataclass, field, fields
from datetime import datetime, timezone
from pathlib import Path


def slugify(name: str) -> str:
    s = re.sub(r"[^\w\s-]", "", name.lower()).strip()
    s = re.sub(r"[\s_-]+", "-", s)
    return s or "chat"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass
class Message:
    id: int
    date: str
    sender_id: int | None
    sender_name: str | None
    text: str
    sender_username: str | None = None
    reply_to: int | None = None
    topic_id: int | None = None
    post_id: int | None = None          # channel post this belongs to (post -> itself, comment -> post)
    media_type: str | None = None       # photo | poll | sticker | voice | audio | video | file | ...
    media_path: str | None = None
    media_meta: dict = field(default_factory=dict)
    mentions: list = field(default_factory=list)  # [{handle, user_id, name, real}]
    extra: dict = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


@dataclass
class ChatMeta:
    chat_id: int
    title: str
    kind: str          # user | group | channel | forum
    slug: str
    fetched_at: str
    message_count: int = 0
    members: int | None = None
    extra: dict = field(default_factory=dict)


class ChatStore:
    def __init__(self, slug: str, root: Path):
        self.dir = root / slug
        self.media_dir = self.dir / "media"
        self.messages_path = self.dir / "messages.jsonl"
        self.meta_path = self.dir / "meta.json"

    def ensure(self) -> None:
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def reset(self) -> None:
        if self.messages_path.exists():
            self.messages_path.unlink()
        if self.media_dir.exists():
            shutil.rmtree(self.media_dir)
        self.media_dir.mkdir(parents=True, exist_ok=True)

    def write_meta(self, meta: ChatMeta) -> None:
        self.meta_path.write_text(json.dumps(asdict(meta), ensure_ascii=False, indent=2), encoding="utf-8")

    def read_meta(self) -> ChatMeta:
        return ChatMeta(**json.loads(self.meta_path.read_text(encoding="utf-8")))

    def append_message(self, m: Message) -> None:
        with self.messages_path.open("a", encoding="utf-8") as f:
            f.write(m.to_json() + "\n")

    def rewrite(self, messages: list[Message]) -> None:
        tmp = self.messages_path.with_suffix(".jsonl.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            for m in messages:
                f.write(m.to_json() + "\n")
        tmp.replace(self.messages_path)

    def load_messages(self) -> list[Message]:
        if not self.messages_path.exists():
            return []
        known = {f.name for f in fields(Message)}
        out = []
        with self.messages_path.open(encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    d = json.loads(line)
                    out.append(Message(**{k: v for k, v in d.items() if k in known}))
        return out


def telegram_chats(root: Path) -> list[str]:
    out = []
    if not root.exists():
        return out
    for d in sorted(root.iterdir()):
        meta = d / "meta.json"
        if meta.exists():
            try:
                if (json.loads(meta.read_text(encoding="utf-8")).get("extra") or {}).get("platform") == "telegram":
                    out.append(d.name)
            except Exception:
                pass
    return out
