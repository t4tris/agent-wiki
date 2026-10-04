#!/usr/bin/env python3
"""Выгрузка сохранённых сообщений (Saved Messages) из Telegram, с фильтром по источнику.

Шаг 1: список источников с количествами
    python3 _toolkit/tools/tg-saved/tg_saved.py --list
Шаг 2: выгрузка сообщений одного источника вместе с картинками/файлами
    python3 _toolkit/tools/tg-saved/tg_saved.py --source "<id|часть названия>" --out out

Креды берутся из creds.json (рядом со скриптом) или из --api-id/--api-hash.
Прокси по умолчанию http://127.0.0.1:12334 (Outline VPN), можно задать --proxy.
"""
import argparse
import asyncio
import json
import os
import re
import sys
from collections import Counter
from datetime import timezone

from telethon import TelegramClient  # type: ignore[import-untyped]
from telethon.tl.types import (  # type: ignore[import-untyped]
    MessageMediaDocument,
    MessageMediaPhoto,
    PeerChannel,
    PeerChat,
    PeerUser,
)

HERE = os.path.dirname(os.path.abspath(__file__))
SESSION = os.path.join(HERE, "tg_saved")
DEFAULT_PROXY = "http://127.0.0.1:12334"


def parse_proxy(value):
    if not value:
        return None
    m = re.match(r"^(?:(?P<scheme>\w+)://)?(?P<host>[^:/]+):(?P<port>\d+)$", value.strip())
    if not m:
        sys.exit(f"Не разобрал прокси: {value!r} (ожидаю http://host:port)")
    return dict(
        proxy_type=(m.group("scheme") or "http").lower(),
        addr=m.group("host"),
        port=int(m.group("port")),
    )


def load_creds(args):
    api_id, api_hash = args.api_id, args.api_hash
    if not (api_id and api_hash):
        path = os.path.join(HERE, "creds.json")
        if os.path.exists(path):
            data = json.load(open(path, encoding="utf-8"))
            api_id = api_id or data.get("api_id")
            api_hash = api_hash or data.get("api_hash")
    if not (api_id and api_hash):
        sys.exit(
            "Нужны api_id и api_hash: создай приложение на https://my.telegram.org/apps "
            "и положи в creds.json вида {\"api_id\": 123456, \"api_hash\": \"...\"}"
        )
    return int(api_id), str(api_hash).strip()


def source_of(msg):
    """(ключ, человекочитаемое имя) исходного чата для сохранённого сообщения."""
    fwd = getattr(msg, "fwd_from", None)
    if fwd is None:
        return ("unknown", "(без источника: сохранено копией)")
    from_id = getattr(fwd, "from_id", None)
    name = getattr(fwd, "from_name", None)
    if from_id is None:
        return ("unknown", name or "(источник скрыт)")
    if isinstance(from_id, PeerChannel):
        return (f"channel:{from_id.channel_id}", name or f"канал {from_id.channel_id}")
    if isinstance(from_id, PeerChat):
        return (f"chat:{from_id.chat_id}", name or f"чат {from_id.chat_id}")
    if isinstance(from_id, PeerUser):
        return (f"user:{from_id.user_id}", name or f"юзер {from_id.user_id}")
    return (str(from_id), name or str(from_id))


def post_link(key, msg_id):
    """Ссылка вида t.me/c/<internal>/<msg_id> для канала/чата."""
    kind, _, raw = key.partition(":")
    if kind in ("channel", "chat"):
        return f"https://t.me/c/{raw}/{msg_id}"
    return ""


def media_kind(msg):
    if isinstance(msg.media, MessageMediaPhoto):
        return "photo"
    if isinstance(msg.media, MessageMediaDocument):
        doc = msg.media.document
        if doc and any(getattr(a, "voice", False) for a in getattr(doc, "attributes", [])):
            return "voice"
        return "file"
    return None


async def cmd_list(client):
    counter = Counter()
    names = {}
    total = 0
    async for msg in client.iter_messages("me"):
        total += 1
        key, name = source_of(msg)
        counter[key] += 1
        names.setdefault(key, name)
    print(f"Всего сохранённых сообщений: {total}\n")
    print(f"{'сообщений':>10}  {'id':<24}  название")
    for key, count in counter.most_common():
        print(f"{count:>10}  {key:<24}  {names.get(key)}")
    out = os.path.join(HERE, "sources.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            [{"key": k, "name": names.get(k), "count": c} for k, c in counter.most_common()],
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nСписок источников записан в {out}")


def matches(key, name, needle):
    needle = needle.strip().lower()
    if needle == key.lower():
        return True
    if key.split(":")[-1] == needle:
        return True
    return needle in (name or "").lower()


async def cmd_export(client, args):
    src_dir = os.path.join(args.out, "media")
    os.makedirs(src_dir, exist_ok=True)
    items = []
    skipped = 0
    async for msg in client.iter_messages("me"):
        key, name = source_of(msg)
        if not matches(key, name, args.source):
            continue
        if args.limit and len(items) >= args.limit:
            break
        entry = {
            "id": msg.id,
            "date": msg.date.astimezone(timezone.utc).isoformat() if msg.date else None,
            "source_key": key,
            "source_name": name,
            "text": msg.message or "",
            "link": post_link(key, msg.id),
            "media": [],
        }
        if msg.media and not args.no_media:
            kind = media_kind(msg)
            if kind in ("photo", "file"):
                path = await msg.download_media(file=src_dir)
                if path:
                    entry["media"].append(os.path.relpath(path, args.out).replace("\\", "/"))
                    entry["media_kind"] = kind
            elif kind:
                entry["media_kind"] = kind
                skipped += 1
        items.append(entry)
        print(f"\rзабрано {len(items)} сообщений...", end="", flush=True)
    print()
    items.sort(key=lambda e: e["date"] or "")
    src = items[0]["source_name"] if items else "-"
    safe = re.sub(r"[^\w.-]+", "_", src).strip("_")[:40] or "export"
    data = {"source": src, "count": len(items), "messages": items}
    json_path = os.path.join(args.out, "messages_" + safe + ".json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    md_path = json_path.replace(".json", ".md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(f"# {src} — {len(items)} сохранённых сообщений\n\n")
        for e in items:
            f.write(f"## {e['date']} (id {e['id']})\n\n")
            if e["text"]:
                f.write(e["text"] + "\n\n")
            for m in e["media"]:
                f.write(f"![]({m})\n\n")
            if e["link"]:
                f.write(f"{e['link']}\n\n")
    print(f"Готово: {len(items)} сообщений -> {json_path}\n        {md_path}")
    if skipped:
        print(f"(у {skipped} сообщений медиа пропущено: голосовые/кружки — скажи, если нужны)")


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api-id")
    ap.add_argument("--api-hash")
    ap.add_argument("--proxy", default=os.environ.get("TG_PROXY", DEFAULT_PROXY))
    ap.add_argument("--phone", default=os.environ.get("TG_PHONE"))
    ap.add_argument("--list", action="store_true", help="показать источники сохранённых сообщений")
    ap.add_argument("--source", help="id источника (channel:123 / user:45) или часть названия")
    ap.add_argument("--out", default=os.path.join(HERE, "out"))
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--no-media", action="store_true")
    args = ap.parse_args()

    api_id, api_hash = load_creds(args)
    proxy = parse_proxy(args.proxy)
    os.makedirs(args.out, exist_ok=True)
    client = TelegramClient(SESSION, api_id, api_hash, proxy=proxy)
    if args.phone:
        await client.start(phone=args.phone)
    else:
        await client.start()
    me = await client.get_me()
    print(f"Вошли как {me.first_name or ''} @{me.username or me.id}\n")
    if args.list or not args.source:
        await cmd_list(client)
    if args.source:
        await cmd_export(client, args)
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
