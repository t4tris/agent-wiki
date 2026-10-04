#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Карта волны инжеста: заметки → страницы, со сменой поколений и разметкой вшитого.

Зачем генератор. Карта волны собиралась вручную, лежала в `_staging/audit/` и со временем показывала удалённые
страницы. Дальше владелец задал два правила, и они определяют устройство файла:

1. **Статус «новая» переходит от поколения к поколению.** Страница новая не «навсегда»: она новая в той волне,
   которая её создала. Ворота волн живут в `_staging/waves.json`; статус страницы считается по дате её создания
   (`git log --diff-filter=A`), а не берётся из записи одной волны.
2. **У дополненной страницы видно, что вшито, а что было.** Для каждой тронутой страницы считается диффом,
   какие разделы волна создала и сколько строк добавила в существующие, — это и показывается в карточке.

Данные и артефакт разделены:
  * `_staging/audit/ingest-wave-2026-09-14.json` — запись волны (что было: страницы, заметки, строки); она же
    служит сверкой: если записанный в ней бейдж расходится с вычисленным по правилу, генератор это печатает;
  * `_staging/waves.json`                        — реестр волн (ворота поколений);
  * `_staging/audit/ingest-wave-map.html`        — производный артефакт, собирается этим генератором.

Запуск:
    python3 _toolkit/ingest_wave_map.py                 # разбор: что покажет карта
    python3 _toolkit/ingest_wave_map.py --write         # записать HTML
    python3 _toolkit/ingest_wave_map.py --record        # пересобрать запись волны из исходной карты
"""
import argparse
import glob
import io
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import fmparse
import toolkit

# Запись волны ищется по образцу имени, а не знается заранее: у каждого экземпляра своя волна и своя дата.
DATA_PATTERN = re.compile(r"^ingest-wave-[A-Za-z0-9_.-]+\.json$")
OUT = "audit/ingest-wave-map.html"
OUT_DISPLAY = os.path.join(toolkit.AREA, "audit", "ingest-wave-map.html")


def find_wave_record(root):
    """Свежая запись волны в каталоге аудита — или None, если волн ещё не было."""
    ad = toolkit.area(root, "audit")
    if not os.path.isdir(ad):
        return None, None
    cand = sorted(f for f in os.listdir(ad) if DATA_PATTERN.match(f))
    if not cand:
        return None, None
    name = cand[-1]
    orig = name.replace(".json", ".original.html")
    orig_path = os.path.join(ad, orig)
    return os.path.join(ad, name), (orig_path if os.path.exists(orig_path) else None)
WAVES = "waves.json"
WAVES_DISPLAY = os.path.join(toolkit.AREA, "waves.json")
DATE_FMT = "%Y-%m-%d %H:%M"


def sh(cmd, root):
    if not os.path.exists(os.path.join(root, ".git")):
        return ""
    probe = subprocess.run(["git", "rev-parse", "--verify", "--quiet", "HEAD"], cwd=root,
                           capture_output=True, text=True, encoding="utf-8", check=False)
    if probe.returncode != 0:
        return ""
    return subprocess.run(cmd, cwd=root, capture_output=True, text=True, encoding="utf-8", check=True).stdout


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def clean(s):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", s)).strip()


# ── волны и поколения ────────────────────────────────────────────────────────────────────────────────
def load_waves(root):
    w = json.load(open(toolkit.area(root, WAVES), encoding="utf-8"))["waves"]
    for x in w:
        x["from"] = x["from"] or "0000-01-01 00:00"
        x["to"] = x["to"] or "9999-01-01 00:00"
    return w


def current_wave(waves):
    for w in waves:
        if w.get("current"):
            return w
    return waves[-1]


def file_commits(root, page):
    """Коммиты файла страницы от новых к старым: (hash, дата-строка, тема)."""
    out = sh(["git", "log", "--format=%h|%cd|%s", "--date=format:" + DATE_FMT, "--follow", "--",
              "wiki/" + page + ".md"], root).strip()
    return [tuple(x.split("|", 2)) for x in out.split("\n") if x]


def created_at(root, page):
    cs = file_commits(root, page)
    return cs[-1][1] if cs else None


def wave_of(waves, when):
    if not when:
        return None
    for w in waves:
        if w["from"] <= when < w["to"]:
            return w
    return None


def status_of(root, page, waves):
    """Статус страницы по правилу поколений.

    created_in  — волна, создавшая страницу;  touched_in — волна, тронувшая её содержимое;
    badge       — НОВАЯ / ДОПОЛНЕННАЯ / «из волны такой-то» / пусто.
    """
    cur = current_wave(waves)
    cs = file_commits(root, page)
    if not cs:
        # Копия без git: истории нет, и `file_commits` пуст. Возвращаем полный набор ключей, иначе панель
        # падала с KeyError 'kind' ровно у скачавшего архив (нашёл входной тест 2026-09-21).
        return {"badge": "", "kind": "—", "created_in": None, "touched_in": None, "commits_in_wave": 0,
                "created_at": None, "touched_at": None}
    born = wave_of(waves, cs[-1][1])
    inside = [c for c in cs if cur["from"] <= c[1] < cur["to"]]
    if born is cur:
        badge, kind = "НОВАЯ", "новая"
    elif inside:
        badge, kind = "ДОПОЛНЕННАЯ", "дополненная"
    elif born:
        badge, kind = f"ИЗ ВОЛНЫ {born['id']}", "прошлая"
    else:
        badge, kind = "", "вне волн"
    return {"badge": badge, "kind": kind, "created_in": born, "touched_in": cur if inside else None,
            "commits_in_wave": len(inside), "created_at": cs[-1][1],
            "touched_at": inside[0][1] if inside else None}


def additions(root, page, waves):
    """Что текущая волна вшила в страницу: разделы, куда попали добавленные строки, и сам текст.

    Добавленные строки привязываются к разделам по номерам строк в новой версии файла, а не по последнему
    заголовку в диффе: заголовок существующего раздела в дифф не попадает, и без этого добавки падали в
    безымянную группу «до первого раздела» и терялись.
    """
    cur = current_wave(waves)
    cs = file_commits(root, page)
    inside = [c for c in cs if cur["from"] <= c[1] < cur["to"]]
    if not inside:
        return None
    before = [c for c in cs if c[1] < cur["from"]]
    head = inside[0][0]
    base = before[0][0] if before else None

    def norm(h):
        h = re.sub(r"\[\[[^\]|]+\|([^\]]+)\]\]", r"\1", h)
        h = re.sub(r"\[\[([^\]]+)\]\]", r"\1", h)
        return re.sub(r"[^0-9a-zA-Zа-яА-ЯёЁ]+", " ", h).strip().lower()

    new_text = io.open(toolkit.wiki(root, page + ".md"), encoding="utf-8").read()
    lines = new_text.split("\n")
    all_ids, added_lines = set(), []
    heads = [(n + 1, norm(m.group(1)), m.group(1)) for n, l in enumerate(lines) if (m := re.match(r"^#{2,3} (.+)$", l))]
    had = set()
    if base:
        had = {norm(m.group(1)) for m in re.finditer(r"(?m)^#{2,3} (.+)$",
                                                     sh(["git", "show", base + ":wiki/" + page + ".md"], root))}

    if base:
        diff = sh(["git", "diff", "-U0", base + ".." + head, "--", "wiki/" + page + ".md"], root)
    else:
        # страница родилась в этой волне: весь её текст и есть вшитое этой волной
        diff = "@@ -0,0 +1,%d @@\n" % len(lines) + "".join("+" + x + "\n" for x in lines)
        added_lines.extend(x for x in lines if x.strip())
    secs, added, removed, edited, new_ids = {}, 0, 0, 0, set()
    for hunk in re.split(r"(?m)^@@", diff)[1:]:
        m = re.match(r" -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", hunk)
        pos = int(m.group(1)) if m else 0
        # ханк без убранных строк — чистая вставка; с убранными — правка (замена), её строки не «вшито»
        is_edit = any(l.startswith("-") and not l.startswith("---") for l in hunk.split("\n"))
        for line in hunk.split("\n"):
            if line.startswith("+") and line[1:].strip():
                added += 1
                all_ids.update(re.findall(r"\b(\d{6})\b", line[1:]))
                added_lines.append(line[1:])
                if is_edit:
                    edited += 1
                name, new = "(до первого раздела)", False
                for n, nm, raw in heads:
                    if n <= pos:
                        name, new = raw, nm not in had
                    else:
                        break
                s = secs.setdefault(name, {"name": name, "new": new, "lines": 0, "edited": 0, "text": []})
                if is_edit:
                    s["edited"] += 1
                else:
                    s["lines"] += 1
                    if len(s["text"]) < 40:
                        s["text"].append(line[1:].strip())
            elif line.startswith("-"):
                removed += 1
            pos += 1
    # источник считается вшитым этой волной, если его упоминание появилось на добавленных строках,
    # а до волны его на странице не было вовсе: переписанная строка таблицы — тоже новая приписка
    base_ids = set(re.findall(r"\b(\d{6})\b", sh(["git", "show", base + ":wiki/" + page + ".md"], root))) if base else set()
    new_ids = all_ids - base_ids
    return {"base": base, "head": head, "added": added - edited, "all_added": added, "edited": edited,
            "new_ids": new_ids, "added_text": "\n".join(added_lines),
            "base_text": sh(["git", "show", base + ":wiki/" + page + ".md"], root) if base else "",
            "removed": removed, "sections": [s for s in secs.values()], "commits": len(inside),
            "since": inside[-1][1], "until": inside[0][1]}


def page_sources(root, page):
    """Источники страницы из её поля `sources:`: (ссылка, метка, id, есть ли файл)."""
    path = toolkit.wiki(root, page + ".md")
    if not os.path.exists(path):
        return []
    declared = fmparse.items(io.open(path, encoding="utf-8", errors="ignore").read(), "sources")
    if not declared:
        return []
    out, seen = [], set()
    for item in declared:
        ref = item.strip().strip('"').strip("'")
        if not ref or ref in seen:
            continue
        seen.add(ref)
        stem = os.path.basename(ref)
        stem = stem[:-3] if stem.endswith(".md") else stem
        ids = re.findall(r"\b(\d{6})\b", stem)
        raw = os.path.join(root, ref)
        mirror = toolkit.wiki(root, "sources", ref)
        out.append({"ref": ref, "stem": stem, "id": ids[-1] if ids else None, "label": ids[-1] if ids else stem,
                    "exists": os.path.exists(raw) or os.path.exists(mirror)})
    return out


def source_label(root, ref):
    """Подпись источника без id: канал и дата (для телеграма) либо заголовок статьи или манифеста."""
    for cand in (os.path.join(root, ref), toolkit.wiki(root, "sources", ref)):
        if os.path.exists(cand):
            text = io.open(cand, encoding="utf-8", errors="ignore").read()[:3000]
            def field(*names, text=text):
                for n in names:
                    m = re.search(r"(?m)^" + n + r":\s*(.+)$", text)
                    if m:
                        return m.group(1).strip().strip('"').strip("'")
                return ""
            ch, dt = field("source_channel"), field("source_date")
            if ch:
                return ch + ((" · " + dt) if dt else "")
            ti = field("title")
            if ti:
                return ti
            h = re.search(r"(?m)^#\s+(.+)$", text)
            return h.group(1).strip() if h else ""
    return ""


def registry_only(root):
    """Источники, объявленные только реестрами: у содержательных страниц их нет.

    Нужны для пунктирного бордера пилюли: такой источник вшит в реестр, но ни в одну страницу по существу.
    """
    pages = {}
    for path in sorted(glob.glob(toolkit.wiki(root, "*", "*.md"))):
        d = os.path.basename(os.path.dirname(path))
        if d.startswith("_") or d == "sources":
            continue
        slug = os.path.relpath(path, toolkit.wiki(root)).replace(os.sep, "/")[:-3]
        m = re.search(r"(?ms)^sources:\s*\[(.*?)\]",
                      io.open(path, encoding="utf-8", errors="ignore").read())
        pages[slug] = [x.strip() for x in m.group(1).split(",") if x.strip()] if m else []
    only = set()
    for src in {s for srcs in pages.values() for s in srcs}:
        users = [slug for slug, srcs in pages.items() if src in srcs]
        if users and all(u.endswith("-registry") for u in users):
            base = os.path.basename(src)
            only.add(base)
            ids = re.findall(r"\b(\d{6})\b", base)
            if ids:
                only.add(ids[-1])
    return only


def pills_html(root, page, notes=(), add=None, all_new=False):
    """Пилюли источников карточки: заметки записи волны и поле `sources:` страницы.

    Метка пилюли — id сообщения, а для источников без id (тексты GRACE, статьи, манифесты) — имя файла.
    Зелёный бордер: источник вшит именно этой волной (для страницы, созданной волной, — все её источники).
    """
    reg_only = registry_only(root)
    new_ids = set(add["new_ids"]) if add else set()
    added_text = (add or {}).get("added_text", "")
    base_text = (add or {}).get("base_text", "")
    shown, out = set(), []
    for n in notes:
        stem = n["stem"] or note_stem(root, n["id"])
        cls = ((" new" if n["id"] in new_ids else "") + ("" if stem else " gone")
               + (" reg-only" if n["id"] in reg_only else ""))
        out.append(f"<span class='n{cls}' title='{esc(n['channel'])}'><b>{esc(n['id'])}</b> "
                   f"{esc((stem or 'источник снят из корпуса')[:46])}</span>")
        shown.add(n["id"])
    for s in page_sources(root, page):
        if s["label"] in shown:
            continue
        shown.add(s["label"])
        fresh = all_new or (s["label"] in added_text and s["label"] not in base_text)
        cls = ((" new" if fresh else "") + ("" if s["exists"] else " gone")
               + (" reg-only" if (os.path.basename(s["ref"]) in reg_only or s["label"] in reg_only) else ""))
        tip = source_label(root, s["ref"]) or ("источник вне корпуса" if not s["exists"] else s["stem"])
        body = "" if s["exists"] else "источник снят из корпуса"
        out.append(f"<span class='n{cls}' title='{esc(tip)}'><b>{esc(s['label'])}</b> {esc(body[:46])}</span>")
    return "".join(out)


def note_stem(root, sid):
    """Короткая подпись заметки: хвост имени файла источника (как у остальных пилюль).

    Нужна там, где в записи волны подписи нет — исходная карта для трёх заметок оставила её пустой,
    и пилюля выглядела как один только номер.
    """
    hits = sorted(glob.glob(toolkit.raw(root, "**", f"*{sid}*.md"), recursive=True))
    if not hits:
        return ""
    name = os.path.basename(hits[0])[:-3]
    name = re.sub(r"^\d{4}-\d{2}-\d{2}-", "", name)
    name = re.sub(r"-" + sid + r"$", "", name)
    return name.replace("-", " ")[:46]


def source_title(root, sid):
    """Откуда источник: канал и дата (source_channel/source_date), иначе заголовок файла."""
    hits = sorted(glob.glob(toolkit.raw(root, "**", f"*{sid}*.md"), recursive=True))
    if not hits:
        return "источник вне корпуса"
    text = io.open(hits[0], encoding="utf-8", errors="ignore").read()[:2000]
    def field(*names):
        for n in names:
            m = re.search(r"(?m)^" + n + r":\s*(.+)$", text)
            if m:
                return m.group(1).strip().strip('"').strip("'")
        return ""
    ch = field("source_channel", "channel", "chat", "канал")
    dt = field("source_date", "date", "дата")
    return " · ".join(x for x in (ch, dt) if x) or field("title") or "источник"


def created_in_wave(root, waves, known):
    """Страницы, созданные в текущей волне, которых нет в записи волны (заведены из решений/кандидатов)."""
    cur = current_wave(waves)
    found = []
    for path in sorted(glob.glob(toolkit.wiki(root, "*", "*.md"))):
        d = os.path.basename(os.path.dirname(path))
        if d.startswith("_") or d == "sources":          # _meta и копии источников — не страницы вики
            continue
        slug = os.path.relpath(path, toolkit.wiki(root)).replace(os.sep, "/")[:-3]
        if slug in known:
            continue
        cs = file_commits(root, slug)
        if cs and cur["from"] <= cs[-1][1] < cur["to"]:
            body = io.open(path, encoding="utf-8", errors="ignore").read()
            src = re.search(r"(?ms)^sources:\s*\[(.*?)\]", body)
            ids = sorted(set(re.findall(r"\b(\d{6})\b", src.group(1)))) if src else []
            found.append({"page": slug, "title": first_heading(path), "created_at": cs[-1][1], "ids": ids})
    return sorted(found, key=lambda x: x["created_at"])


def old_pages(root, waves, known):
    """Страницы, которых не касалась эта волна: поколение по волне, создавшей страницу.

    Статус «дополненная» здесь не ставится: страницы вне записи волны текущая волна не наполняла, а правки
    сопровождения (ссылки, выравнивание) — не работа волны.
    """
    out = []
    for path in sorted(glob.glob(toolkit.wiki(root, "*", "*.md"))):
        d = os.path.basename(os.path.dirname(path))
        if d.startswith("_") or d == "sources":
            continue
        slug = os.path.relpath(path, toolkit.wiki(root)).replace(os.sep, "/")[:-3]
        if slug in known:
            continue
        cs = file_commits(root, slug)
        if not cs:
            continue
        born = wave_of(waves, cs[-1][1])
        out.append({"page": slug, "title": first_heading(path), "created_at": cs[-1][1],
                    "badge": f"ИЗ ВОЛНЫ {born['id']}" if born else "ВНЕ ВОЛН",
                    "sources": len(page_sources(root, slug))})
    return sorted(out, key=lambda x: (x["badge"] != "ВНЕ ВОЛН", x["badge"], x["page"]))


def first_heading(path):
    text = io.open(path, encoding="utf-8", errors="ignore").read()
    for line in text.split("\n"):
        if line.startswith("# "):
            return line[2:].strip().strip('"')
    return os.path.basename(path)[:-3]


# ── состояние страницы и приписка заметок ────────────────────────────────────────────────────────────
def page_state(root, page):
    path = toolkit.wiki(root, page + ".md")
    if os.path.exists(path):
        return {"state": "есть", "page": page}
    log = sh(["git", "log", "--diff-filter=DR", "--follow", "--name-status", "--format=@%h|%ad|%s",
              "--date=format:%Y-%m-%d", "--", "wiki/" + page + ".md"], root)
    deleted, renamed, when, subject = None, None, "", ""
    for line in log.split("\n"):
        if line.startswith("@"):
            if not when:
                _, when, subject = (line.split("|", 2) + ["", "", ""])[:3]
        elif line.startswith("D"):
            deleted = deleted or (when, subject)
        elif line.startswith("R"):
            parts = line.split("\t")
            if len(parts) > 2:
                renamed = renamed or (parts[2], when, subject)
    if renamed and os.path.exists(os.path.join(root, renamed[0])):
        return {"state": "переименована", "page": renamed[0], "when": renamed[1], "why": renamed[2]}
    return {"state": "удалена", "when": (deleted or (when, subject))[0],
            "why": (deleted or (when, subject))[1] or subject}


def notes_now(root, notes):
    landed, unknown = set(), []
    for n in notes:
        hits = glob.glob(toolkit.wiki(root, "sources", "raw", "**", f"*{n['id']}.md"), recursive=True)
        if not hits:
            unknown.append(n["id"])
            continue
        text = io.open(sorted(set(hits))[0], encoding="utf-8", errors="ignore").read()
        i = text.find("Где использован")
        found = re.findall(r"\[\[([a-z0-9][a-z0-9\-]*)\]\]", text[i:] if i >= 0 else "")
        if not found:
            unknown.append(n["id"])
        landed.update(found)
    return sorted(landed), unknown


# ── запись волны из исходной карты ───────────────────────────────────────────────────────────────────
def extract_record(root):
    """Собрать запись волны из исходной ручной карты.

    Сторож: число разобранных страниц обязано совпасть с числом слагов в разметке. Дефект первого разбора
    был именно такой — регулярка требовала класс в `class='pg new'`, блоки дополненных страниц (`class='pg'`)
    не попадали, и карта молча теряла 12 страниц из 21.
    """
    data_path, orig_path = find_wave_record(root)
    if not orig_path:
        sys.exit("нет исходной карты волны (audit/ingest-wave-<дата>.original.html) — пересобирать нечего")
    src = io.open(orig_path, encoding="utf-8").read()
    chunks = re.findall(r"(?s)<div class='pg([^']*)'>(.*?)(?=<div class='pg|</div>\s*</body>)", src)
    slugs = re.findall(r"<code class='pth'>(.*?)</code>", src)
    if len(chunks) != len(slugs):
        raise SystemExit(f"разбор исходной карты неполон: блоков {len(chunks)}, слагов {len(slugs)} — "
                         f"проверь разметку, а не записывай усечённую запись")
    pages = []
    for kind, ch in chunks:
        ttl = re.search(r"(?s)<h3>(.*?)</h3>", ch)
        slug = re.search(r"<code class='pth'>(.*?)</code>", ch)
        meta = re.search(r"(?s)<div class='meta'>(.*?)</div>", ch)
        ln = re.search(r"строк\s*(\d+)", clean(meta.group(1))) if meta else None
        bd = re.search(r"<span class='badge ([^']+)'>(.*?)</span>", ch)
        more = re.search(r"<span class='more'>(.*?)</span>", ch)
        pages.append({
            "page": slug.group(1).strip() if slug else "",
            "title": clean(ttl.group(1)) if ttl else "",
            "kind": {"new": "новая", "old": "существующая", "": "дополненная"}.get(kind.strip(), kind.strip()),
            "lines": int(ln.group(1)) if ln else None,
            "notes": [{"channel": a.replace("&amp;", "&"), "id": b, "stem": clean(c)}
                      for a, b, c in re.findall(r"(?s)<span class='n' title='(.*?)'><b>(\d+)</b>(.*?)</span>", ch)],
            "meta": clean(meta.group(1)) if meta else "",
            "more": clean(more.group(1)) if more else "",
            "badge_class": bd.group(1) if bd else "",
            "badge_text": clean(bd.group(2)) if bd else "",
        })
    head = src[:src.find("<div class='pg")]
    sub = re.search(r"(?s)<div class='sub'>(.*?)</div>", src)
    foot = re.search(r"(?s)<div class='foot'>(.*?)</div>", src)
    return {"wave": "инжест 2026-09-14/15 (заметки → страницы)", "recorded": "2026-09-15",
            "source_artifact": os.path.relpath(orig_path, root).replace(os.sep, "/"),
            "title": re.search(r"<title>(.*?)</title>", src).group(1),
            "subtitle": clean(sub.group(1)) if sub else clean(head)[:400],
            "foot": clean(foot.group(1)) if foot else "", "pages": pages}


# ── отрисовка ────────────────────────────────────────────────────────────────────────────────────────
def render(root, data_path=None):
    data_path = data_path or find_wave_record(root)[0]
    if not data_path:
        sys.exit("записи волны нет: карта волны собирается только после первой волны")
    data = json.load(open(data_path, encoding="utf-8"))
    waves = load_waves(root)
    cur = current_wave(waves)
    alive, closed, ghosts, mismatches = [], [], [], []
    for p in data["pages"]:
        st = page_state(root, p["page"]) if p["page"] else {"state": "нет данных"}
        landed, unknown = notes_now(root, p["notes"])
        ps = status_of(root, p["page"], waves) if p["page"] and os.path.exists(
            toolkit.wiki(root, p["page"] + ".md")) else {"badge": "", "kind": "—"}
        recorded = (p.get("badge_text") or "").replace(" · РЕЕСТР", "").replace(" · РАЗДЕЛ 8", "")
        if recorded and ps["badge"] and recorded != ps["badge"]:
            mismatches.append((p["page"], recorded, ps["badge"]))
        if st["state"] in ("удалена", "переименована"):
            ghosts.append((p["page"], st))
        sub_badges = [x for x in ("РЕЕСТР", "РАЗДЕЛ 8") if x in (p.get("badge_text") or "")]
        badge_cls = {"НОВАЯ": "b-new", "ДОПОЛНЕННАЯ": "b-up"}.get(ps.get("badge"), "b-prev")
        badge_txt = ps["badge"] + (" · " + " · ".join(sub_badges) if sub_badges and ps["badge"] else "")
        add = additions(root, p["page"], waves) if ps.get("kind") in ("дополненная", "новая") else None
        add_html = ""
        if add and ps.get("kind") == "новая":
            add_html = (f"<div class='add'><div class='addh'>создана целиком в этой волне "
                        f"({esc(cur['label'])}): +{add['added']} строк</div></div>")
        elif add and add["sections"]:
            rows_add = "".join(
                f"<li class='{'newsec' if s['new'] else 'oldsec'}'>"
                f"{'новый раздел' if s['new'] else 'вписано в существующий раздел'}: "
                f"<b>{esc(s['name'].lstrip('# '))}</b>"
                + (f" — {s['lines']} строк" if s['lines'] else "")
                + (f" (правок {s['edited']})" if s.get('edited') else "") + "</li>"
                for s in add["sections"] if s["name"] != "(до первого раздела)"
                and (s["new"] and (s["lines"] or s.get("edited")) or s["lines"] or s.get("edited")))
            ins = "".join(
                f"<div class='insg'><div class='inss'>{esc(s['name'].lstrip('# '))}</div>"
                + "".join(f"<div class='insl'>{esc(x)}</div>" for x in s["text"]) + "</div>"
                for s in add["sections"] if s["name"] != "(до первого раздела)" and s["text"])
            add_html = (f"<div class='add'><div class='addh'>вшито в этой волне ({esc(cur['label'])}): "
                        f"<b>{add['added']} новых строк</b>"
                        + (f" · правок {add['edited']}" if add.get("edited") else "")
                        + (f" · убрано {add['removed']}" if add["removed"] else "")
                        + f" · коммитов {add['commits']}</div><ul class='secs'>{rows_add}</ul>"
                        + (f"<details class='ins'><summary>показать, что именно вшито ({add['added']} строк)</summary>"
                           f"{ins}</details>" if ins else "")
                        + "</div>")
        if st["state"] == "есть":
            status, where = "<span class='st ok'>есть</span>", ""
        elif st["state"] == "переименована":
            status = "<span class='st warn'>переименована</span>"
            where = (f"<div class='where'>теперь <code>[[{esc(st['page'].replace('wiki/', '').rsplit('.', 1)[0])}]]"
                     f"</code> · {esc(st.get('when', ''))}</div>")
        else:
            status = "<span class='st bad'>УДАЛЕНА</span>"
            where = (f"<div class='where'>удалена {esc(st.get('when', ''))}: {esc((st.get('why') or '')[:150])}"
                     + (f"<br>заметки волны сейчас: " + ", ".join(f"<code>[[{s}]]</code>" for s in landed) if landed else "")
                     + (f"<br>без страницы: {', '.join(unknown)}" if unknown else "") + "</div>")
        notes_html = pills_html(root, p["page"], p["notes"], add)
        # Пометка «… ещё N» остаётся только там, где полного списка действительно нет: у закрытой страницы
        # (её поле sources: вместе с ней) или когда страница не объявила источников. Иначе карточка показывает
        # все пилюли, и пометка об усечении была бы ложью.
        keep_more = bool(p.get("more")) and (st["state"] != "есть" or not page_sources(root, p["page"]))
        more_text = p.get("more", "")
        if keep_more and st["state"] != "есть":
            n = re.search(r"(\d+)", more_text)
            more_text = (f"… ещё {n.group(1) if n else '?'} заметок записи волны не показаны: "
                         f"список был в самой странице, а страница {st['state']}")
        more_html = f"<span class='more'>{esc(more_text)}</span>" if keep_more else ""
        cls = {"новая": "new", "дополненная": "up"}.get(ps["kind"], "prev")
        row = (f"<div class='pg {cls}'><div class='ph'>"
               f"<span class='badge {badge_cls}'>{esc(badge_txt)}</span><h3>{esc(p['title'])}</h3>"
               f"<span class='stcell'>{status}</span></div>"
               f"<code class='pth'>{esc(p['page'] if st['state'] == 'есть' else p['page'].split('/')[-1])}</code>"
               f"<div class='meta'>{esc(p['meta'])}</div>"
               f"<div class='gen'>{'создана ' + esc(ps.get('created_at', '')[:10]) if ps['kind'] == 'новая' else ('создана ' + esc(ps.get('created_at', '')[:10]) + ' · тронута ' + esc((ps.get('touched_at') or '')[:10]) if ps.get('touched_at') else '')}</div>"
               f"{add_html}{where}"
               f"<div class='ns'>{notes_html}{more_html}</div></div>")
        (closed if st["state"] in ("удалена", "переименована") else alive).append(row)
    known = {p["page"] for p in data["pages"]}
    extra = created_in_wave(root, waves, known)
    old = old_pages(root, waves, known | {e["page"] for e in extra})
    extra_html = ""
    if extra:
        extra_html = ("<section class='extra-new'><h2 class='gh'>Создано в этой волне вне записи волны: "
                      + str(len(extra)) + " страниц — новое поколение, заведённое не инжестом "
                        "(из решений по кандидатам и замыслам)</h2><div class='grid'>"
                      + "".join(f"<div class='pg new'><div class='ph'>"
                                f"<span class='badge b-new'>НОВАЯ</span><h3>{esc(e['title'])}</h3></div>"
                                f"<code class='pth'>{esc(e['page'])}</code>"
                                f"<div class='gen'>создана {esc(e['created_at'][:10])}"
                                + (f" · источников {len(e['ids'])}" if e.get("ids") else "") + "</div>"
                                + (f"<div class='ns'>{pills_html(root, e['page'], (), None, all_new=True)}</div>"
                                   if e.get("ids") or page_sources(root, e["page"]) else "")
                                + "</div>"
                                for e in extra) + "</div></section>")
    old_html = ""
    if old:
        old_html = ("<section class='old-gen'><h2 class='gh'>Прошлые поколения: страницы, которых эта волна "
                    "не касалась — " + str(len(old)) + " (показаны для полноты картины)</h2><div class='grid'>"
                    + "".join(f"<div class='pg prev'><div class='ph'>"
                              f"<span class='badge b-prev'>{esc(o['badge'])}</span>"
                              f"<h3>{esc(o['title'])}</h3></div>"
                              f"<code class='pth'>{esc(o['page'])}</code>"
                              f"<div class='gen'>создана {esc(o['created_at'][:10])}"
                              + (f" · источников {o['sources']}" if o["sources"] else "") + "</div>"
                              + (f"<div class='ns'>{pills_html(root, o['page'])}</div>" if o["sources"] else "")
                              + "</div>" for o in old) + "</div></section>")
    warn = ""
    if ghosts:
        warn += ("<div class='warn'><b>Внимание:</b> карта волны — факт прошлого, но часть её страниц с тех пор "
                 "закрыта: " + "; ".join(f"<code>{esc(g[0].split('/')[-1])}</code> — {esc(g[1]['state'])}"
                                         for g in ghosts) + ". Материал заметок перечислен в строках ниже.</div>")
    if mismatches:
        warn += ("<div class='warn'><b>Расхождение бейджей:</b> запись волны и правило поколений говорят разное — "
                 + "; ".join(f"<code>{esc(m[0])}</code>: записано «{esc(m[1])}», по правилу «{esc(m[2])}»"
                             for m in mismatches) + "</div>")
    closed_block = ""
    if closed:
        closed_block = ("<details class='closed'><summary>Закрытые с тех пор страницы (" + str(len(closed))
                        + "): удалены или переименованы — раскрыть, чтобы увидеть, что было и куда ушёл материал"
                        "</summary><div class='grid'>" + "".join(closed) + "</div></details>")
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>{esc(data['title'])}</title>
<style>
:root {{ color-scheme:dark; --bg:#0f131b; --card:#161d29; --line:#232e40; --ink:#e8eef5; --muted:#8b98a8;
        --new:#7ee787; --up:#79b8ff; --reg:#d2a8ff; --ok:#7ee787; --warn:#e0a33a; --bad:#ff7b72; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:18px; background:var(--bg); color:var(--ink);
       font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif; }}
h1 {{ font-size:18px; margin:0 0 6px; }}
h2.gh {{ font-size:15px; margin:22px 0 10px; color:var(--muted); }}
.sub {{ color:var(--muted); font-size:13px; margin-bottom:12px; }}
.warn {{ border:1px solid var(--warn); background:#2a2213; color:#f0d9a8; padding:10px 12px;
         border-radius:10px; margin-bottom:14px; }}
section.old-gen {{ margin:30px 0 0; padding-top:20px; border-top:1px solid var(--line); }}
section.old-gen .pg {{ opacity:.75; }}
section.extra-new {{ margin:0 0 30px; padding-bottom:20px; border-bottom:1px solid var(--line); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(430px,1fr)); gap:14px; }}
.pg {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:12px 14px; }}
.pg.new {{ border-color:color-mix(in srgb, var(--new) 45%, transparent); }}
.pg.up {{ border-color:color-mix(in srgb, var(--up) 30%, transparent); }}
.pg.prev {{ opacity:.82; }}
.ph {{ display:flex; align-items:baseline; gap:8px; flex-wrap:wrap; }}
h3 {{ font-size:14px; margin:0; flex:1 1 auto; }}
.badge {{ font-size:11px; padding:1px 7px; border-radius:999px; border:1px solid var(--line); color:var(--muted); }}
.b-new {{ background:color-mix(in srgb, var(--new) 18%, transparent); color:var(--new); border-color:transparent; }}
.b-up {{ background:color-mix(in srgb, var(--up) 18%, transparent); color:var(--up); border-color:transparent; }}
.b-reg {{ background:color-mix(in srgb, var(--reg) 18%, transparent); color:var(--reg); border-color:transparent; }}
.b-prev {{ color:var(--muted); }}
.st {{ font-size:11px; padding:1px 7px; border-radius:999px; }}
.st.ok {{ background:#12301f; color:var(--ok); }}
.st.warn {{ background:#2a2213; color:var(--warn); }}
.st.bad {{ background:#33191a; color:var(--bad); }}
.pth {{ color:var(--muted); font-size:12px; }}
.meta {{ color:var(--muted); font-size:12px; margin:4px 0 6px; }}
.gen {{ color:var(--muted); font-size:11.5px; margin-bottom:6px; }}
.add {{ border-left:2px solid color-mix(in srgb, var(--new) 55%, transparent); background:#131a15;
       border-radius:8px; padding:7px 9px; margin:6px 0; }}
.addh {{ color:var(--new); font-size:12px; margin-bottom:4px; }}
ul.secs {{ margin:0; padding-left:18px; }}
ul.secs li {{ font-size:12px; margin:1px 0; }}
li.newsec {{ color:var(--new); }}
details.ins {{ margin-top:6px; }}
details.ins summary {{ cursor:pointer; color:var(--muted); font-size:12px; }}
.insg {{ margin:6px 0 0 8px; border-left:2px solid color-mix(in srgb, var(--new) 35%, transparent); padding-left:8px; }}
.inss {{ color:var(--muted); font-size:11.5px; margin-bottom:2px; }}
.insl {{ font-size:12px; color:#cfe3d3; background:#111813; border-radius:4px; padding:1px 6px; margin:1px 0;
        white-space:pre-wrap; }}
li.oldsec {{ color:var(--up); }}
.where {{ font-size:12px; color:#f0d9a8; background:#221c10; border-radius:8px; padding:6px 8px; margin:6px 0; }}
.ns {{ display:flex; flex-wrap:wrap; gap:6px; }}
.n {{ font-size:11px; color:var(--muted); background:#12161d; border:1px solid var(--line);
     border-radius:999px; padding:1px 7px; max-width:100%; overflow:hidden; white-space:nowrap; text-overflow:ellipsis; }}
.n b {{ color:var(--up); font-weight:600; }}
.n.new {{ border-color:color-mix(in srgb, var(--new) 70%, transparent); color:#cfe3d3; }}
.n.new b {{ color:var(--new); }}
.n.reg-only {{ border-style:dashed; border-color:color-mix(in srgb, var(--muted) 70%, transparent);
              opacity:.5; }}
.n.reg-only:hover {{ opacity:1; }}
.n.new.reg-only {{ border-color:color-mix(in srgb, var(--new) 55%, transparent); }}
.n.gone {{ border-style:dashed; text-decoration:line-through; text-decoration-color:var(--bad); }}
.lg {{ padding:1px 6px; border-radius:999px; border:1px solid var(--line); }}
.lg.new {{ border-color:color-mix(in srgb, var(--new) 60%, transparent); color:var(--new); }}
.lg.reg {{ border-style:dashed; color:var(--muted); }}
.lg.up {{ border-color:color-mix(in srgb, var(--up) 60%, transparent); color:var(--up); }}
.more {{ font-size:12px; color:var(--muted); font-style:italic; align-self:center; padding:2px 9px; }}
.foot {{ color:var(--muted); font-size:12px; margin-top:24px; max-width:1100px; }}
details.closed {{ margin-top:16px; border:1px solid var(--line); border-radius:12px; background:#12161d; padding:10px 12px; }}
details.closed summary {{ cursor:pointer; color:var(--muted); font-size:13px; }}
details.closed[open] summary {{ margin-bottom:10px; }}
</style></head><body>
<h1>{esc(data['title'])}</h1>
<div class="sub">{esc(data.get('subtitle', ''))}<br>
Поколения: текущая волна — <b>{esc(cur['label'])}</b> (с {esc(cur['from'])}, открыта). Статус «новая» считается
по дате создания страницы и уходит к новому поколению, когда открывается следующая волна (реестр `{esc(WAVES_DISPLAY)}`).
Запись волны: <code>{esc(os.path.relpath(data_path, root))}</code> · состояния и диффы проверены по репозиторию сейчас.<br>
Разметка: <span class='lg new'>зелёная рамка карточки и зелёный бордер пилюли</span> — страница создана в этой волне /
источник вшит в этой волне; <span class='lg up'>синеватая рамка</span> — страница была раньше и дополнена волной;
погашенная карточка — страница из прошлой волны, которую текущая не трогала;
<span class='lg reg'>пунктирный бордер пилюли</span> — источник объявлен только реестром, содержательных страниц
у него нет.</div>
{warn}
{extra_html}
<div class="grid">{''.join(alive)}</div>
{old_html}
<div class="foot">{esc(data.get('foot', ''))}</div>
{closed_block}
</body></html>
"""


def snapshot_record(root):
    """Собрать запись текущей волны из состояния репозитория: страницы, заметки, строки.

    Зачем. Первая запись волны появляться сама не умеет: `--record` пересобирает её из исходной
    ручной карты, которой у новой волны нет. Найдено 2026-09-25 на волне 1 AgentWiki: запись пришлось
    писать скриптом-однодневкой. Теперь её строит механизм: страницы слоя знаний — из дерева,
    заметки — из шапок `sources:` страниц (канал — корпус под `raw/`, stem — заголовок записи),
    вид — по правилу поколений (`status_of`). Числа меряются, а не набираются.
    """
    try:
        waves = load_waves(root)
    except (OSError, ValueError, KeyError):
        waves = []
    pages = []
    for d in ("concepts", "entities", "comparisons", "queries"):
        folder = toolkit.wiki(root, d)
        if not os.path.isdir(folder):
            continue
        for fn in sorted(os.listdir(folder)):
            if not fn.endswith(".md"):
                continue
            rel = "%s/%s" % (d, fn[:-3])
            text = io.open(os.path.join(folder, fn), encoding="utf-8").read()
            m = re.match(r"(?s)^---\n(.*?)\n---", text)
            head, title, srcs = (m.group(1) if m else ""), fn[:-3], []
            for line in head.split("\n"):
                if line.startswith("title:"):
                    title = line.split(":", 1)[1].strip().strip('"')
            sm = re.search(r"(?s)sources:\s*\[(.*?)\]", head)
            if sm:
                srcs = [s.strip() for s in sm.group(1).split(",") if s.strip()]
            lines = len([line for line in text.split("\n") if line.strip()])
            notes = []
            for src in srcs:
                rp = os.path.join(root, *src.split("/"))
                stem = os.path.basename(src)
                if os.path.isfile(rp):
                    mt = re.match(r"(?s)^---\n(.*?)\n---", io.open(rp, encoding="utf-8").read())
                    if mt:
                        for line in mt.group(1).split("\n"):
                            if line.startswith("title:"):
                                stem = line.split(":", 1)[1].strip().strip('"')
                parts = src.split("/")
                notes.append({"channel": parts[1] if len(parts) > 2 and parts[0] == "raw" else "",
                              "id": os.path.splitext(os.path.basename(src))[0], "stem": stem})
            badge = status_of(root, rel, waves)["badge"] if waves else "НОВАЯ"
            kind = {"НОВАЯ": "новая", "ДОПОЛНЕННАЯ": "дополненная"}.get(badge, "существующая")
            pages.append({"page": rel, "title": title, "kind": kind, "lines": lines,
                          "notes": notes, "meta": "", "more": ""})
    today = __import__("datetime").date.today().isoformat()
    return {"wave": "волна %s" % today, "recorded": today,
            "title": "Запись волны %s: страниц %d" % (today, len(pages)),
            "subtitle": "Собрано механизмом из состояния репозитория.",
            "foot": "", "pages": pages}


def main():
    ap = argparse.ArgumentParser(description="Пересборка карты волны инжеста.")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--record", action="store_true",
                    help="пересобрать запись волны из исходной карты (audit/…original.html) со сторожем счёта")
    ap.add_argument("--snapshot", action="store_true",
                    help="собрать запись текущей волны из состояния репозитория (страницы, заметки, строки)")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    if a.snapshot:
        rec = snapshot_record(root)
        data_path = toolkit.area(root, "audit",
                                 f"ingest-wave-{__import__('datetime').date.today().isoformat()}.json")
        with io.open(data_path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(rec, ensure_ascii=False, indent=1))
        print(f"запись волны собрана: страниц {len(rec['pages'])}, "
              f"заметок {sum(len(p['notes']) for p in rec['pages'])} → {os.path.relpath(data_path, root)}")
        return 0
        rec = extract_record(root)
        data_path = find_wave_record(root)[0] or toolkit.area(root, "audit",
                                                              f"ingest-wave-{__import__('datetime').date.today().isoformat()}.json")
        with io.open(data_path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(rec, ensure_ascii=False, indent=1))
        print(f"запись волны пересобрана: страниц {len(rec['pages'])}, "
              f"заметок {sum(len(p['notes']) for p in rec['pages'])} → {os.path.relpath(data_path, root)}")
        return 0
    wave_path = find_wave_record(root)[0]
    if not wave_path:
        print("записи волны нет: её собирает `wave_runner.py close --confirm` (снимок состояния репозитория)")
        return 1
    html = render(root, wave_path)
    data = json.load(open(wave_path, encoding="utf-8"))
    waves = load_waves(root)
    print(f"карта волны: страниц в записи {len(data['pages'])}, заметок "
          f"{sum(len(p['notes']) for p in data['pages'])}, текущая волна {current_wave(waves)['id']}")
    for p in data["pages"]:
        if p["page"] and os.path.exists(toolkit.wiki(root, p["page"] + ".md")):
            st = status_of(root, p["page"], waves)
            add = additions(root, p["page"], waves)
            print(f"  {st['badge']:<16} {p['page']:<44} создана {(st.get('created_at') or '—')[:10]}"
                  + (f" | вшито +{add['added']} строк в {sum(1 for s in add['sections'] if s['new'])} разделов"
                     if add else ""))
    if not a.write:
        print("(без --write карта не записана)")
        return 0
    with io.open(toolkit.area(root, OUT), "w", encoding="utf-8", newline="") as f:
        f.write(html)
    print("записано:", OUT_DISPLAY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
