#!/usr/bin/env python3
"""Адрес из выгрузки — в запись. Заметка теряла ссылку, а с ней и весь свой смысл.

Зачем. Телеграм-пост «Забираем себе — тут.» ссылается на курс гиперссылкой. Выгрузка (`links.json`) адрес
знает, а запись в `raw/` — нет: в тексте стоит «тут» без адреса, и дальше по цепочке (карточка, строка
реестра, страница) материал выглядит как «непонятно что, к изучению». Смысл заметки — сама ссылка.

    python3 _toolkit/links_into_records.py --wiki .              # показать, у каких записей адрес не доехал
    python3 _toolkit/links_into_records.py --wiki . --write      # вписать адрес в запись на место ссылки

Пишет только в записи `raw/` (и в их зеркала), по одному адресу на запись: тот, что выгрузка привязывает к
id сообщения. Ничего не сочиняет: если адреса в выгрузке нет, запись не трогается.
"""
import argparse
import glob
import hashlib
import io
import json
import os
import re
import sys

import raw_write
import toolkit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Корень канала (https://t.me/имя) адресом материала не считается: на него ссылается шапка поста.
CHANNEL_ROOT = re.compile(r"^https?://t\.me/(?!\+|joinchat/)[^/]+/?$")

LINK_HINT = re.compile(r"(?i)(тут|здесь|по\s+ссылке|ссылка|забираем|полная\s+версия|подробнее)")


def load_links(root):
    """id сообщения -> адрес, привязанный выгрузкой (links.json)."""
    by_id = {}
    # Каноническая карта — в репозитории (`_tools/tg-saved/links-by-message.json`), рабочая выгрузка
    # лежит вне версионного контроля; читаем обе, каноническая первая.
    for p in [os.path.join(root, "_tools", "tg-saved", "links-by-message.json")] + \
             glob.glob(toolkit.area(root, "telegram", "*", "links.json")):
        try:
            data = json.load(io.open(p, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as ex:
            print(f"предупреждение: карта ссылок {p} пропущена ({ex})", file=sys.stderr)
            continue
        # Две формы одной карты: рабочая выгрузка хранится как «адрес → {ids: [...]}», а каноническая карта в
        # репозитории — как «id сообщения → [адреса]». Одну и ту же карту писали дважды и по-разному, и в свежей
        # копии поставки проверка не видела ничего, потому что рабочий файл лежит вне версионного контроля.
        for key, v in data.items():
            if isinstance(v, dict):                     # форма «адрес → {ids: [...]}»
                for mid in (v.get("ids") or []):
                    by_id.setdefault(int(mid), []).append(key)
            elif isinstance(v, list) and str(key).isdigit():   # форма «id → [адреса]»
                for url in v:
                    by_id.setdefault(int(key), []).append(url)
    return by_id


def records(root):
    for p in glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True):
        txt = io.open(p, encoding="utf-8", errors="replace").read()
        m = re.search(r"(?m)^source_message_id:\s*(\d+)", txt)
        if m:
            yield p, int(m.group(1)), txt


def transform_record(text, urls):
    content = [u for u in urls if not CHANNEL_ROOT.match(u)] or urls
    head_end = text.find("\n---", 4)
    if head_end < 0:
        return text, []
    head, body = text[:head_end], text[head_end:]
    changed, notes = False, []
    if [u for u in content if u not in text]:
        m_sl = re.search(r"(?m)^source_links:\s*\[(.*)\]", head)
        m_su = re.search(r'(?m)^source_url:\s*"([^"]+)"', head)
        if m_sl:
            have = [x.strip().strip('"') for x in m_sl.group(1).split(",") if x.strip()]
            allu = have + [u for u in content if u not in have]
            head = head[:m_sl.start()] + "source_links: [" + ", ".join('"%s"' % u for u in allu) + "]" + head[m_sl.end():]
            notes.append("source_links (дополнено)")
        elif m_su:
            allu = [m_su.group(1)] + [u for u in content if u != m_su.group(1)]
            head = head[:m_su.start()] + "source_links: [" + ", ".join('"%s"' % u for u in allu) + "]" + head[m_su.end():]
            notes.append("source_url → source_links")
        elif len(content) == 1:
            head = re.sub(r"(?m)^(source_message_id:.*)$",
                          lambda m: m.group(1) + '\nsource_url: "%s"' % content[0], head, count=1)
            notes.append("source_url")
        else:
            head = re.sub(r"(?m)^(source_message_id:.*)$",
                          lambda m: m.group(1) + "\nsource_links: [" + ", ".join('"%s"' % u for u in content) + "]",
                          head, count=1)
            notes.append("source_links")
        changed = True
    if not any(u in body for u in content) and LINK_HINT.search(body):
        new_body = re.sub(r"(?i)(—\s*тут\.)", "— тут: %s." % content[0], body, count=1)
        if new_body == body:
            new_body = re.sub(r"(?i)(тут\.)", "тут: %s." % content[0], body, count=1)
        if new_body != body:
            body = new_body
            changed = True
            notes.append("адрес в теле")
    if not changed:
        return text, []
    digest = hashlib.sha256(body.lstrip("\n").encode("utf-8")).hexdigest()
    return re.sub(r"(?m)^sha256:.*$", "sha256: " + digest, head + body), notes


def main():
    ap = argparse.ArgumentParser(description="Вписать адрес из выгрузки в запись")
    ap.add_argument("--wiki", default=ROOT)
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    by_id = load_links(root)
    plans, issues, missing = [], [], []
    for path, mid, text in records(root):
        urls = by_id.get(mid)
        if not urls:
            continue
        new_text, notes = transform_record(text, urls)
        if not notes:
            continue
        rel = os.path.relpath(path, root).replace("\\", "/")
        mirror = toolkit.wiki(root, "sources", rel)
        if os.path.exists(mirror) and raw_write.read(mirror) != text:
            issues.append(f"отказ: зеркало расходится: {rel}")
            continue
        targets = [path] + ([mirror] if os.path.exists(mirror) else [])
        for target in targets:
            old = text if target == path else raw_write.read(target)
            desired = new_text if target == path else transform_record(old, urls)[0]
            raw_write.plan(target, desired, False, "", "адрес из карты", plans, issues, allow_existing=True)
        missing.append((rel, mid, urls, ", ".join(notes)))
    for issue in issues:
        print(issue)
    if issues:
        return 1
    print("записей, где адрес из карты не попал в запись: %d" % len(missing))
    for rel, mid, urls, notes in missing:
        print("  %s (id %d) -> %s" % (rel, mid, "; ".join(u[:60] for u in urls[:2])))
    if a.write:
        print("записано файлов: %d" % raw_write.apply(plans))
    elif missing:
        print("\nэто показ. Вписать: повтори с --write")
    return 0



if __name__ == "__main__":
    sys.exit(main())
