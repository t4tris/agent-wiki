#!/usr/bin/env python3
"""Шаг 1: разбор экспорта Telegram Desktop -> нормализованные сообщения + файл на ревью.

    python3 _toolkit/tools/tg-saved/import_telegram.py --export  <папка с result.json>  --out <staging>

На выходе в staging:
    messages.json  - все сообщения в нормализованном виде
    review.csv     - таблица с колонкой approve (yes/no) для ручной отметки
    review.md      - та же таблица, но читаемо, с картинками
    media/         - медиа кандидатов
    stats.txt      - сводка
Дальше: отметить approve в review.csv и запустить promote_approved.py
"""
import argparse
import csv
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

TOOLKIT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, TOOLKIT)
import toolkit

ROOT = toolkit.root()

# Слова темы экземпляра (сильные, средние, шум) объявляет сам экземпляр: механизм не знает предмета чужой
# вики. Файл — `_staging/tg-vocabulary.local.json`; нет его — инструмент останавливается и говорит об этом,
# а не подставляет молча чужой словарь.
VOCAB_DEFAULT = os.path.join(toolkit.AREA, "tg-vocabulary.local.json")
VOCAB: dict[str, list[str]] = {}


def load_vocabulary(path):
    if not os.path.exists(path):
        sys.exit(f"нет словаря тем экземпляра: заполните {path} (образец — в README выгрузки)")
    try:
        data = json.load(open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        sys.exit(f"словарь тем не прочитан ({path}): {ex}")
    missing = [k for k in ("strong", "medium") if not data.get(k)]
    if missing:
        sys.exit(f"в словаре тем нет обязательных ключей: {', '.join(missing)} ({path})")
    return data


def _inline(node):
    """Инлайн-узел rich-формата (новый экспорт Telegram) -> markdown-строка."""
    if node is None:
        return ""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        # не-строки отбрасываем: неизвестный узел лучше потерять, чем уронить разбор всего экспорта
        return "".join(x for x in (_inline(i) for i in node) if isinstance(x, str))
    if not isinstance(node, dict):
        return str(node)
    kind = node.get("type")
    if kind in ("plain", "text"):
        return _inline(node.get("text"))
    if kind == "text_link":
        label = _inline(node.get("text")) or node.get("href", "")
        return f"[{label}]({node.get('href', '')})"
    if kind in ("bold", "italic", "underline", "strikethrough", "code", "pre", "spoiler", "blockquote", "sub", "sup", "marked"):
        inner = _inline(node.get("text"))
        return {
            "bold": f"**{inner}**", "italic": f"*{inner}*", "underline": f"__{inner}__",
            "strikethrough": f"~~{inner}~~", "code": f"`{inner}`",
            "pre": f"\n```\n{inner}\n```\n", "spoiler": f"||{inner}||",
            "blockquote": f"> {inner}", "sub": inner, "sup": inner, "marked": f"=={inner}==",
        }[kind]
    if kind in ("mention", "hashtag", "url", "email", "phone", "cashtag", "bot_command", "custom_emoji", "emoji"):
        inner = _inline(node.get("text"))
        return (inner if isinstance(inner, str) and inner else "") or node.get("href") or ""
    if "text" in node:
        return _inline(node["text"])
    return ""


def rich_text_of(rm):
    """rich_message -> (текст, медиа). В этом формате нет обычных полей text/photo."""
    out, media = [], []

    def cap_of(block):
        c = block.get("caption")
        if isinstance(c, dict):
            return re.sub(r"\s+", " ", _inline(c.get("text"))).strip()
        return ""

    for b in rm.get("blocks") or []:
        kind = b.get("type")
        if kind == "photo":
            f = b.get("photo") or ""
            cap = cap_of(b)
            if f:
                media.append(f)
                out.append(f"![{cap}]({f})" + (f"\n\n{cap}" if cap else ""))
            elif cap:
                out.append(cap)
        elif kind in ("image", "video", "audio", "voice", "document", "sticker", "animation"):
            f = b.get("photo") or b.get("file") or (b.get("document") or {}).get("file") or ""
            cap = cap_of(b)
            if f:
                media.append(f)
                out.append(f"[{kind}: {f}]" + (f"\n\n{cap}" if cap else ""))
            elif cap:
                out.append(cap)
        elif kind in ("list", "list_item"):
            items = b.get("items") or ([b] if "text" in b else [])
            for it in items:
                line = re.sub(r"\s+", " ", _inline(it.get("text") if isinstance(it, dict) else it)).strip()
                if line:
                    out.append("- " + line)
        elif kind == "table":
            for row in b.get("rows") or []:
                cells = row.get("cells") if isinstance(row, dict) else row
                cells = [re.sub(r"\s+", " ", _inline(c)).strip() for c in (cells or [])]
                if any(cells):
                    out.append("| " + " | ".join(cells) + " |")
        elif kind in ("pre", "code"):
            txt = _inline(b.get("text"))
            if txt.strip():
                out.append("```\n" + txt.strip("\n") + "\n```")
        else:
            txt = _inline(b.get("text"))
            if txt.strip():
                out.append(txt)
    text = "\n\n".join(x.strip("\n") for x in out if x and x.strip())
    return text, [m for m in media if m]


def text_of(raw):
    t = raw.get("text")
    if isinstance(t, list):
        parts = []
        for p in t:
            parts.append(p if isinstance(p, str) else str(p.get("text", "")))
        return "".join(parts)
    if t:
        return t
    rm = raw.get("rich_message")
    if isinstance(rm, dict):
        return rich_text_of(rm)[0]
    return t or ""


def rich_media_of(raw):
    rm = raw.get("rich_message")
    return rich_text_of(rm)[1] if isinstance(rm, dict) else []


def score(text):
    low = text.lower()
    hits, strong, medium = [], 0, 0
    for k in VOCAB["strong"]:
        if k in low:
            strong += 1
            hits.append(k)
    for k in VOCAB.get("medium", []):
        if k in low:
            medium += 1
            hits.append(k)
    noise = sum(1 for n in VOCAB.get("noise", []) if n in low)
    value = strong * 3 + medium * 1 - noise * 4
    return value, hits[:6], strong, medium, noise


def verdict_of(value, strong):
    if strong >= 2 or value >= 7:
        return "relevant", "high"
    if strong >= 1 or value >= 4:
        return "relevant", "medium"
    if value >= 2:
        return "borderline", "low"
    return "out", "high"


def tags_of(hits):
    """Теги записи по совпавшим словам темы: карта «слово -> тег» — слова экземпляра."""
    m = VOCAB.get("tags") or {}
    out = []
    for h in hits:
        for k, v in m.items():
            if k in h and v not in out:
                out.append(v)
    return out[:4] or ["unclassified"]


def collect(export_dir):
    path = os.path.join(export_dir, "result.json")
    if not os.path.exists(path):
        sys.exit(f"Не нашёл {path} — укажи папку, куда Telegram сохранил экспорт")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    msgs, skipped = [], 0
    for raw in data.get("messages", []):
        if raw.get("type") != "message":
            skipped += 1
            continue
        text = text_of(raw).strip()
        media = []
        for key in ("photo", "file", "sticker", "video_file", "audio_file", "voice_message"):
            v = raw.get(key)
            if isinstance(v, str):
                media.append(v)
        for f in rich_media_of(raw):
            if f not in media:
                media.append(f)
        src = raw.get("saved_from") or raw.get("forwarded_from") or raw.get("from") or "(без источника)"
        ts = raw.get("date_unixtime")
        try:
            dt = datetime.fromtimestamp(int(ts), tz=timezone.utc)
            iso = dt.strftime("%Y-%m-%dT%H:%M:%S")
            day = dt.strftime("%Y-%m-%d")
        except (TypeError, ValueError):
            iso, day = raw.get("date", ""), (raw.get("date", "") or "")[:10]
        value, hits, strong, medium, noise = score(text)
        verdict, conf = verdict_of(value, strong)
        if not text and media:
            verdict, conf = ("media-only", "manual")
        msgs.append({
            "id": raw.get("id"),
            "day": day,
            "date": iso,
            "source": src,
            "text": text,
            "media": media,
            "score": value,
            "verdict": verdict,
            "confidence": conf,
            "tags": tags_of(hits),
            "hits": hits,
        })
    msgs.sort(key=lambda m: m["date"])
    return msgs, skipped, data


def preview(text, n=260):
    t = re.sub(r"\s+", " ", text).strip()
    return t[:n] + ("…" if len(t) > n else "")


def main():
    global VOCAB
    ap = argparse.ArgumentParser()
    ap.add_argument("--vocabulary", default=VOCAB_DEFAULT,
                    help="словарь тем экземпляра (по умолчанию _staging/tg-vocabulary.local.json)")
    ap.add_argument("--export", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", choices=["candidates", "all"], default="candidates",
                    help="кого показывать в review (по умолчанию candidates = relevant+borderline+media-only)")
    args = ap.parse_args()
    VOCAB = load_vocabulary(args.vocabulary)

    os.makedirs(args.out, exist_ok=True)
    media_dir = os.path.join(args.out, "media")
    os.makedirs(media_dir, exist_ok=True)
    msgs, skipped, meta = collect(args.export)

    with open(os.path.join(args.out, "messages.json"), "w", encoding="utf-8") as f:
        json.dump({"chat": meta.get("name"), "count": len(msgs), "messages": msgs},
                  f, ensure_ascii=False, indent=2)

    cands = [m for m in msgs if m["verdict"] != "out"] if args.only == "candidates" else msgs
    # медиа кандидатов
    for m in cands:
        if m["verdict"] != "media-only":
            continue
        for rel in m["media"]:
            src = os.path.join(args.export, rel)
            if os.path.exists(src):
                dst = os.path.join(media_dir, os.path.basename(rel))
                if not os.path.exists(dst):
                    shutil.copy2(src, dst)
                m.setdefault("media_local", []).append(f"media/{os.path.basename(rel)}")

    rows = []
    for i, m in enumerate(cands, 1):
        rows.append({
            "num": i, "approve": "", "date": m["day"], "source": m["source"],
            "verdict": m["verdict"], "confidence": m["confidence"],
            "tags": " ".join(m["tags"]), "chars": len(m["text"]),
            "id": m["id"], "preview": preview(m["text"], 300),
        })

    csv_path = os.path.join(args.out, "review.csv")
    with open(csv_path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["num", "approve", "date", "source", "verdict", "confidence",
                            "tags", "chars", "id", "preview"])
        w.writeheader()
        w.writerows(rows)

    order = {"high": 0, "medium": 1, "low": 2, "manual": 3}
    groups = {"relevant": [], "borderline": [], "media-only": []}
    for r in rows:
        groups.get(r["verdict"], groups["borderline"]).append(r)
    md = [f"# Триаж сохранённых сообщений: {meta.get('name', 'Saved Messages')}", ""]
    md.append(f"Всего сообщений: {len(msgs)} (служебных пропущено: {skipped}). "
              f"Кандидатов на ревью: {len(rows)}. "
              f"В тему: {sum(1 for r in rows if r['verdict'] == 'relevant')}, "
              f"на грани: {sum(1 for r in rows if r['verdict'] == 'borderline')}, "
              f"только медиа: {sum(1 for r in rows if r['verdict'] == 'media-only')}.")
    md.append("")
    md.append("Отмечай одобренные в `review.csv` (колонка `approve` = yes), "
              "потом запусти `promote_approved.py`.")
    for vtitle, key in (("В тему", "relevant"), ("На грани", "borderline"), ("Только медиа", "media-only")):
        items = sorted(groups.get(key, []), key=lambda r: order.get(r["confidence"], 9))
        if not items:
            continue
        md.append(f"\n## {vtitle} ({len(items)})\n")
        for r in items:
            md.append(f"### {r['num']}. {r['date']} · {r['source']}")
            md.append(f"`{r['verdict']}/{r['confidence']}` · теги: {r['tags']} · {r['chars']} симв.")
            if r["preview"]:
                md.append("")
                md.append(preview(r["preview"], 1200))
            md.append("")
    with open(os.path.join(args.out, "review.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    stats = {
        "chat": meta.get("name"), "total_messages": len(msgs), "skipped_service": skipped,
        "candidates": len(rows),
        "relevant": sum(1 for r in rows if r["verdict"] == "relevant"),
        "borderline": sum(1 for r in rows if r["verdict"] == "borderline"),
        "media_only": sum(1 for r in rows if r["verdict"] == "media-only"),
        "out": sum(1 for m in msgs if m["verdict"] == "out"),
    }
    with open(os.path.join(args.out, "stats.txt"), "w", encoding="utf-8") as f:
        for k, v in stats.items():
            f.write(f"{k}: {v}\n")
    print(json.dumps(stats, ensure_ascii=False, indent=2))
    print(f"\nГотово: {csv_path} и {os.path.join(args.out, 'review.md')}")


if __name__ == "__main__":
    main()
