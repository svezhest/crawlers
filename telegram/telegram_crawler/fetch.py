"""Telethon side: resolve one chat, confirm, download; incremental update; media backfill.

Only the chat you name is touched. Broadcast channels: each post is fetched together with
its discussion-thread comments (tied by post_id). --from/--to bound by message date (UTC).
"""
from __future__ import annotations

import asyncio
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from rich.console import Console
from rich.prompt import Confirm
from telethon import TelegramClient
from telethon.errors import FloodWaitError, RPCError
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.messages import GetStickerSetRequest
from telethon.tl.types import (Channel, Chat, DocumentAttributeAudio, DocumentAttributeSticker,
                               DocumentAttributeVideo, MessageMediaContact, MessageMediaDocument,
                               MessageMediaGeo, MessageMediaPhoto, MessageMediaPoll,
                               MessageMediaWebPage, User)

from .config import Config
from .storage import ChatMeta, ChatStore, Message, now_iso, slugify, telegram_chats

console = Console()
_MENTION_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9_]{3,32})")
_MENTION_SKIP = {"me", "all", "everyone"}
_MEDIA_GROUPS = {"photos": {"photo"}, "audio": {"audio", "voice"}, "files": {"file", "video"}}


def _client(cfg: Config) -> TelegramClient:
    cfg = cfg.require()
    proxy = cfg.proxy()
    if proxy:
        console.print(f"[dim]proxy {proxy['addr']}:{proxy['port']}[/dim]")
    return TelegramClient(cfg.session, cfg.api_id, cfg.api_hash, proxy=proxy)


def _kind(e) -> str:
    if isinstance(e, User):
        return "user"
    if isinstance(e, Chat):
        return "group"
    if isinstance(e, Channel):
        if getattr(e, "forum", False):
            return "forum"
        return "channel" if e.broadcast else "group"
    return "unknown"


def _title(e) -> str:
    if isinstance(e, User):
        return " ".join(filter(None, [e.first_name, e.last_name])) or (e.username or f"user:{e.id}")
    return getattr(e, "title", str(e.id))


async def _confirm(client, entity, yes: bool) -> bool:
    try:
        last = await client.get_messages(entity, limit=1)
        preview = last[0].message[:80] if last and last[0].message else "(no text)"
    except Exception:
        preview = "(unavailable)"
    console.rule("[bold yellow]Chat to download")
    console.print(f"  Title : [bold]{_title(entity)}[/bold]\n  ID    : {entity.id}\n"
                  f"  Kind  : {_kind(entity)}\n  Last  : {preview}")
    console.rule()
    return yes or Confirm.ask("Download this chat?", default=False)


async def _get_entity_floodsafe(client, handle: str, max_wait: int = 300):
    try:
        return await client.get_entity(handle)
    except FloodWaitError as e:
        if e.seconds > max_wait:
            raise
        console.print(f"  [dim]flood-wait {e.seconds}s on @{handle}[/dim]")
        await asyncio.sleep(e.seconds + 1)
        return await client.get_entity(handle)


async def _resolve_mentions(client, text: str, cache: dict) -> list[dict]:
    out, seen = [], set()
    for m in _MENTION_RE.finditer(text or ""):
        handle = m.group(1)
        low = handle.lower()
        if low in _MENTION_SKIP or low in seen:
            continue
        seen.add(low)
        if low not in cache:
            try:
                ent = await _get_entity_floodsafe(client, handle)
                cache[low] = {"handle": handle, "user_id": ent.id if isinstance(ent, User) else None,
                              "name": _title(ent), "real": isinstance(ent, User)}
            except Exception:
                cache[low] = {"handle": handle, "user_id": None, "name": None, "real": False}
        out.append(dict(cache[low]))
    return out


def _text_of(x):
    return getattr(x, "text", x) or ""


def _poll_meta(media) -> dict:
    poll, results = media.poll, media.results
    voters = {r.option: r.voters for r in (results.results or [])} if results else {}
    return {"question": _text_of(poll.question),
            "options": [{"text": _text_of(a.text), "voters": voters.get(a.option)} for a in poll.answers],
            "total_voters": getattr(results, "total_voters", None),
            "closed": bool(getattr(poll, "closed", False)), "quiz": bool(getattr(poll, "quiz", False)),
            "multiple": bool(getattr(poll, "multiple_choice", False))}


async def _sticker_pack(client, stickerset, cache: dict):
    key = getattr(stickerset, "id", None)
    if key not in cache:
        try:
            cache[key] = (await client(GetStickerSetRequest(stickerset=stickerset, hash=0))).set.title
        except Exception:
            cache[key] = None
    return cache[key]


async def _describe_media(client, msg, store: ChatStore, photo_paths: dict, pack_cache: dict, photos: bool):
    media = msg.media
    if media is None:
        return None, None, {}
    if isinstance(media, MessageMediaPhoto):
        if not photos:
            return "photo", None, {}
        key = getattr(media.photo, "id", None)
        if key is not None and key in photo_paths:
            return "photo", photo_paths[key], {"resend": True}
        try:
            path = await msg.download_media(file=str(store.media_dir))
        except Exception as e:
            console.print(f"  [yellow]skip photo[/yellow] (msg {msg.id}): {e}")
            return "photo", None, {"download_failed": True}
        path = str(path) if path else None
        if key is not None and path:
            photo_paths[key] = path
        return "photo", path, {}
    if isinstance(media, MessageMediaPoll):
        return "poll", None, _poll_meta(media)
    if isinstance(media, MessageMediaDocument):
        doc = media.document
        attrs = {type(a).__name__: a for a in (doc.attributes or [])}
        fname = getattr(attrs.get("DocumentAttributeFilename"), "file_name", None)
        if DocumentAttributeSticker.__name__ in attrs:
            s = attrs[DocumentAttributeSticker.__name__]
            return "sticker", None, {"emoji": s.alt, "pack": await _sticker_pack(client, s.stickerset, pack_cache)}
        if DocumentAttributeAudio.__name__ in attrs:
            a = attrs[DocumentAttributeAudio.__name__]
            return ("voice" if getattr(a, "voice", False) else "audio"), None, {
                "filename": fname, "title": getattr(a, "title", None),
                "performer": getattr(a, "performer", None), "duration": getattr(a, "duration", None)}
        if DocumentAttributeVideo.__name__ in attrs:
            return "video", None, {"filename": fname}
        return "file", None, {"filename": fname, "mime": getattr(doc, "mime_type", None)}
    if isinstance(media, MessageMediaContact):
        return "contact", None, {"name": f"{media.first_name or ''} {media.last_name or ''}".strip(),
                                 "phone": media.phone_number}
    if isinstance(media, MessageMediaGeo):
        return "geo", None, {"lat": getattr(media.geo, "lat", None), "long": getattr(media.geo, "long", None)}
    if isinstance(media, MessageMediaWebPage):
        return None, None, {}
    return "other", None, {"kind": type(media).__name__}


async def _build(client, msg, post_id, *, store, photo_paths, pack_cache, mention_cache, photos, mentions):
    sender = sender_name = sender_username = None
    try:
        s = await msg.get_sender()
        if s is not None:
            sender, sender_name, sender_username = s.id, _title(s), getattr(s, "username", None)
    except Exception:
        pass
    media_type, media_path, media_meta = await _describe_media(client, msg, store, photo_paths, pack_cache, photos)
    ments = await _resolve_mentions(client, msg.message or "", mention_cache) if mentions else []
    return Message(id=msg.id, date=(msg.date.isoformat() if msg.date else now_iso()), sender_id=sender,
                   sender_name=sender_name, sender_username=sender_username, text=msg.message or "",
                   reply_to=getattr(getattr(msg, "reply_to", None), "reply_to_msg_id", None),
                   topic_id=getattr(getattr(msg, "reply_to", None), "reply_to_top_id", None),
                   post_id=post_id, media_type=media_type, media_path=media_path,
                   media_meta=media_meta, mentions=ments)


def _parse_range(from_s, to_s):
    def p(s, label):
        if not s:
            return None
        try:
            return date.fromisoformat(s.strip())
        except ValueError:
            raise SystemExit(f"Invalid --{label} date '{s}' — use YYYY-MM-DD.")
    frm, to = p(from_s, "from"), p(to_s, "to")
    if frm and to and frm > to:
        raise SystemExit(f"--from ({frm}) is after --to ({to}).")
    return frm, to


def _to_offset(to):
    return datetime(to.year, to.month, to.day, tzinfo=timezone.utc) + timedelta(days=1) if to else None


def _before(msg, frm) -> bool:
    return bool(frm and msg.date and msg.date.date() < frm)


def _in_range(msg, frm, to) -> bool:
    d = msg.date.date() if msg.date else None
    return d is None or not ((frm and d < frm) or (to and d > to))


async def _has_comments(client, entity) -> bool:
    if _kind(entity) != "channel":
        return False
    try:
        full = await client(GetFullChannelRequest(entity))
        return getattr(full.full_chat, "linked_chat_id", None) is not None
    except RPCError:
        return False


async def _iter_chat(client, entity, *, limit, min_id, frm, to, on_message):
    """Walk newest-first; for channels also walk each post's comment thread."""
    comments = await _has_comments(client, entity)
    channel = _kind(entity) == "channel"
    kw = {"limit": limit, "offset_date": _to_offset(to)}
    if min_id:
        kw["min_id"] = min_id
    async for msg in client.iter_messages(entity, **kw):
        if _before(msg, frm):
            break
        if not _in_range(msg, frm, to):
            continue
        await on_message(msg, msg.id if channel else None)
        if comments:
            try:
                async for c in client.iter_messages(entity, reply_to=msg.id):
                    await on_message(c, msg.id)
            except RPCError:
                pass


async def _pick_dialog(client, query: str):
    q = query.lower().strip()
    matches = [d async for d in client.iter_dialogs() if not q or q in (d.name or "").lower()]
    if not matches:
        raise SystemExit(f"No dialogs match '{query}'.")
    console.rule("[bold]Matching chats")
    for i, d in enumerate(matches, 1):
        console.print(f"  [bold]{i:>3}[/bold]  {d.name}  [dim]({_kind(d.entity)}, id={d.entity.id})[/dim]")
    raw = console.input("Pick a number (blank to abort): ").strip()
    if not raw.isdigit() or not (1 <= int(raw) <= len(matches)):
        raise SystemExit("Aborted.")
    return matches[int(raw) - 1].entity


async def fetch_chat(cfg: Config, root: Path, query: str, *, limit, photos, pick, from_date, to_date,
                     yes, mentions) -> None:
    frm, to = _parse_range(from_date, to_date)
    client = _client(cfg)
    await client.start()
    try:
        if pick:
            entity = await _pick_dialog(client, query)
        else:
            try:
                entity = await client.get_entity(query)
            except Exception as e:
                raise SystemExit(f"Could not resolve '{query}': {e}\nPrivate group without @handle? Use --pick.")
        if not await _confirm(client, entity, yes):
            raise SystemExit("Aborted — not confirmed.")
        title = _title(entity)
        store = ChatStore(slugify(title), root)
        store.ensure()
        store.reset()
        caches = dict(photo_paths={}, pack_cache={}, mention_cache={})
        count = 0

        async def on_message(msg, post_id):
            nonlocal count
            store.append_message(await _build(client, msg, post_id, store=store, photos=photos,
                                              mentions=mentions, **caches))
            count += 1
            if count % 200 == 0:
                console.print(f"  ... {count} messages")

        span = f" [{from_date or '…'} → {to_date or '…'}]" if (frm or to) else ""
        console.print(f"[green]Downloading[/green] {title}{span} ...")
        await _iter_chat(client, entity, limit=limit, min_id=0, frm=frm, to=to, on_message=on_message)
        store.write_meta(ChatMeta(chat_id=entity.id, title=title, kind=_kind(entity), slug=store.dir.name,
                                  fetched_at=now_iso(), message_count=count,
                                  members=getattr(entity, "participants_count", None),
                                  extra={"platform": "telegram"}))
        console.print(f"[bold green]Done[/bold green] — {count} messages -> {store.dir}")
    finally:
        await client.disconnect()


async def _resolve_by_id(client, meta: ChatMeta):
    try:
        return await client.get_entity(meta.chat_id)
    except Exception:
        async for d in client.iter_dialogs():
            if getattr(d.entity, "id", None) == meta.chat_id:
                return d.entity
    raise SystemExit(f"Could not resolve '{meta.slug}' (id {meta.chat_id}). Open it once in Telegram, then retry.")


def _targets(root: Path, targets: list[str]) -> list[str]:
    slugs = telegram_chats(root) if targets == ["all"] else targets
    if not slugs:
        raise SystemExit("No Telegram chats found under the output directory.")
    return slugs


async def update_chats(cfg: Config, root: Path, targets: list[str], *, photos, from_date, to_date, mentions) -> None:
    """Append messages newer than the last stored id. New comments on OLD channel posts are
    not refreshed (that needs a full re-fetch)."""
    frm, to = _parse_range(from_date, to_date)
    client = _client(cfg)
    await client.start()
    try:
        for slug in _targets(root, targets):
            store = ChatStore(slug, root)
            if not store.meta_path.exists():
                console.print(f"[yellow]skip[/yellow] {slug}: no meta.json")
                continue
            meta = store.read_meta()
            last_id = max((m.id for m in store.load_messages()), default=0)
            entity = await _resolve_by_id(client, meta)
            caches = dict(photo_paths={}, pack_cache={}, mention_cache={})
            new: list[Message] = []

            async def on_message(msg, post_id):
                new.append(await _build(client, msg, post_id, store=store, photos=photos,
                                        mentions=mentions, **caches))

            console.print(f"[green]Updating[/green] {slug} (since id {last_id}) ...")
            await _iter_chat(client, entity, limit=None, min_id=last_id, frm=frm, to=to, on_message=on_message)
            for m in reversed(new):
                store.append_message(m)
            meta.message_count += len(new)
            meta.fetched_at = now_iso()
            store.write_meta(meta)
            console.print(f"[bold green]{slug}[/bold green]: +{len(new)} new")
    finally:
        await client.disconnect()


async def reresolve(cfg: Config, root: Path, targets: list[str]) -> None:
    client = _client(cfg)
    await client.start()
    try:
        for slug in _targets(root, targets):
            store = ChatStore(slug, root)
            if not store.meta_path.exists():
                continue
            msgs = store.load_messages()
            unresolved = {m["handle"].lower() for msg in msgs for m in (msg.mentions or [])
                          if not m.get("real") and m.get("handle")}
            if not unresolved:
                console.print(f"[dim]{slug}[/dim]: nothing unresolved")
                continue
            console.print(f"[green]Re-resolving[/green] {slug} ({len(unresolved)} handles) ...")
            cache: dict = {}
            for msg in msgs:
                if msg.mentions:
                    msg.mentions = await _resolve_mentions(client, msg.text, cache)
            store.rewrite(msgs)
            console.print(f"[bold green]{slug}[/bold green]: {sum(1 for c in cache.values() if c['real'])}/{len(cache)} real")
    finally:
        await client.disconnect()


def _has_file(store: ChatStore, m: Message) -> bool:
    if not m.media_path:
        return False
    p = Path(m.media_path)
    return p.exists() or (store.dir / m.media_path).exists()


async def upgrade(cfg: Config, root: Path, slugs: list[str], media: str) -> None:
    want = set().union(*_MEDIA_GROUPS.values()) if media == "all" else _MEDIA_GROUPS.get(media)
    if want is None:
        raise SystemExit("Unknown --media. Use photos | audio | files | all.")
    client = _client(cfg)
    await client.start()
    try:
        for slug in _targets(root, slugs):
            store = ChatStore(slug, root)
            if not store.meta_path.exists():
                continue
            store.ensure()
            meta, msgs = store.read_meta(), store.load_messages()
            need = [m for m in msgs if m.media_type in want and not _has_file(store, m)]
            if not need:
                console.print(f"{slug}: nothing to backfill for '{media}'")
                continue
            entity = await _resolve_by_id(client, meta)
            console.print(f"[green]Backfilling[/green] {slug}: {len(need)} {media} ...")
            done = 0
            for i, m in enumerate(need, 1):
                try:
                    tgmsg = await client.get_messages(entity, ids=m.id)
                    if tgmsg is None or tgmsg.media is None:
                        continue
                    path = await tgmsg.download_media(file=str(store.media_dir))
                except Exception as e:
                    console.print(f"  [yellow]skip[/yellow] msg {m.id}: {e}")
                    continue
                if path:
                    m.media_path, done = str(path), done + 1
                if i % 100 == 0:
                    console.print(f"  ... {i}/{len(need)}")
            store.rewrite(msgs)
            console.print(f"[bold green]{slug}[/bold green]: {done}/{len(need)} downloaded")
    finally:
        await client.disconnect()
