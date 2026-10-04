#!/usr/bin/env python3
"""Архив слепков пакета и проверка утверждений о прошлом.

Две команды:

    python3 _toolkit/handover.py archive  --wiki . --package <файл> [--date YYYY-MM-DD] [--note "..."]
    python3 _toolkit/handover.py check    --wiki .

`archive` кладёт точную копию пакета в `_staging/audit/handover/<дата>-<sha12>.md` и дописывает
строку в реестр передач (дата, файл, полный sha256, число разделов линтера, страниц,
источников знаний, рёбер, карточек, вопросов, снапшот-строка).

Зачем: файл `audit-package-<дата>.md` перезаписывается в течение дня (11.09 у него две версии:
20 разделов в 21:17 и 21 раздел в 21:47), поэтому «архивного пакета» как такового не было —
и утверждение «в пятом пакете говорилось 21» оказалось непроверяемым по факту отправки.

`check` сверяет утверждения вида «в пакете <дата> … <число> <метрика>» с реестром слепков
и падает, если число не совпало или для даты нет слепка. Это делает прозу о прошлом
машинно проверяемой, как числа настоящего.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import sys

import toolkit

LEDGER = ""        # имя реестра передач объявляет экземпляр: `registers.handover` в domain.local.tsv
COLUMNS = ("date", "file", "sha256", "lint_sections", "pages", "sources", "edges", "cards", "questions", "snapshot", "kind")

METRIC_PATTERNS = (
    (r"раздел\w*\s+линтер\w*", "lint_sections"),
    (r"страниц", "pages"),
    (r"источник\w*\s+знани", "sources"),
    (r"рёб\w*", "edges"),
    (r"карточ\w*", "cards"),
    (r"эталонн\w*\s+вопрос\w*", "questions"),
)


def read(p):
    return open(p, encoding="utf-8", errors="replace").read()


def ledger_path(root):
    declared = _domain.register_path(root, "handover")
    if declared:
        return declared
    return toolkit.area(root, "audit", LEDGER) if LEDGER else ""


def load_ledger(root):
    p = ledger_path(root)
    if not os.path.exists(p):
        return []
    rows = [l.rstrip("\n").split("\t") for l in read(p).splitlines() if l.strip()]
    head, body = rows[0], rows[1:]
    return [dict(zip(head, r)) for r in body]


def figures_of(root):
    import glob
    fs = sorted(glob.glob(toolkit.area(root, "audit", "wiki-figures-*.json")))
    if not fs:
        return {}
    return json.load(open(fs[-1], encoding="utf-8")).get("figures", {})


def snapshot_of(root):
    import glob
    for p in sorted(glob.glob(toolkit.area(root, "audit", "wiki-figures-*.md")), reverse=True):
        m = re.search(r"> Снапшот пакета[^\n]*", read(p))
        if m:
            return m.group(0)
    return ""


def cmd_archive(root, package, date, note, kind="manual"):
    hdir = toolkit.area(root, "audit", "handover")
    os.makedirs(hdir, exist_ok=True)
    raw = open(package, "rb").read()
    digest = hashlib.sha256(raw).hexdigest()
    date = date or re.search(r"(\d{4}-\d{2}-\d{2})", os.path.basename(package)).group(1)
    target = os.path.join(hdir, f"{date}-{digest[:12]}.md")
    if not os.path.exists(target):
        shutil.copyfile(package, target)
    text = raw.decode("utf-8", errors="replace")
    m_sect = re.search(r'"sections":\s*(\d+)', text) or re.search(r"разделов линтера\s*[:=]?\s*(\d+)", text)
    # числа берём ИЗ САМОГО СЛЕПКА (его снапшот-строка), а не из сегодняшних фигур:
    # иначе в реестре прошлого пакета стояли бы сегодняшние значения — ровно та ошибка, от которой уходим.
    snap_line = re.search(r"> Снапшот пакета[^\n]*", text)
    cur = figures_of(root)
    def _num(pattern, fallback):
        if snap_line:
            mm = re.search(pattern, snap_line.group(0))
            if mm:
                return mm.group(1)
        return cur.get(fallback, "—")
    row = {"date": date, "file": os.path.basename(target), "sha256": digest,
           "lint_sections": m_sect.group(1) if m_sect else "—",
           "pages": _num(r"^(?:> Снапшот пакета \(\d{4}-\d{2}-\d{2}\):\s*)?(\d+) страниц", "страницы"),
           "sources": _num(r"=\s*(\d+) источника знаний", "источников знаний"),
           "edges": _num(r"рёбер графа\s*(\d+)", "рёбер графа"),
           "cards": _num(r"карточек\s*(\d+)", "карточек извлечения"),
           "questions": _num(r"эталонных вопросов\s*(\d+)", "эталонных вопросов"),
           "snapshot": re.sub(r"\t", " ", snapshot_of(root))[:180] + (f" | {note}" if note else ""),
           "kind": "auto" if (note or "").startswith("автослепок") else "manual"}
    lp = ledger_path(root)
    new = not os.path.exists(lp)
    with open(lp, "a", encoding="utf-8", newline="") as f:
        if new:
            f.write("\t".join(COLUMNS) + "\n")
        f.write("\t".join(str(row.get(c, "")) for c in COLUMNS) + "\n")
    print(f"слепок: handover/{os.path.basename(target)} | sha256 {digest[:16]}… | разделов {row['lint_sections']}")
    return 0


def cmd_mark_sent(root, date=None, quiet=False):
    """Отмечает отправленный слепок: ровно одна строка на дату.

    Отправка — акт доставки аудитору, отдельный от резервной копии: `offsite.py push` её НЕ помечает
    (push — это копия, несколько push в день не должны выглядеть отправками). Помечает задача
    `_toolkit/send.py`, которая собирает посылку и записывает состав в sent-artifacts.tsv.
    """
    import datetime as _dt
    date = date or _dt.date.today().isoformat()
    lp = ledger_path(root)
    lines = [l for l in read(lp).splitlines() if l.strip()]
    head, rows = lines[0].split("\t"), [l.split("\t") for l in lines[1:]]
    if "kind" not in head:
        head.append("kind")
        rows = [r + ["manual"] for r in rows]
    ki = head.index("kind")
    today_rows = [r for r in rows if r[0] == date]
    if any(r[ki] == "sent" for r in today_rows) and not quiet:
        print(f"за {date} уже есть отправленная строка — добавляю ещё одну: повторная отправка в один день "
              f"штатна, различать версии будут хеши в sent-artifacts.tsv")
        return
    if not today_rows:
        if not quiet:
            print(f"за {date} в реестре нет ни одной строки — отмечать нечего")
        return
    today_rows[-1][ki] = "sent"          # самый свежий снимок этой даты и есть отправленный
    with open(lp, "w", encoding="utf-8", newline="") as f:
        f.write("\t".join(head) + "\n")
        for r in rows:
            f.write("\t".join(r) + "\n")
    if not quiet:
        print(f"отмечено как отправленное: {today_rows[-1][1]} ({date})")


def cmd_check(root, quiet=False):
    """Сверяет прозу о прошлых пакетах с реестром слепков."""
    led = load_ledger(root)
    # Сверяем утверждения о прошлом ТОЛЬКО с отправленными строками: автослепок — состояние рабочего
    # дерева, отправленное — обещание аудитору. Заведено по ответу 1 разбора девятого цикла.
    sent_rows = [r for r in led if (r.get("kind") or "manual") == "sent"]
    by_date = {}
    for r in sent_rows:
        by_date.setdefault(r["date"], []).append(r)
    problems = []
    # Повторные отправки в один день разрешены (рецепт «исправить + опубликовать дельту»): проверка
    # «ровно одна отправленная строка на дату» была моим изобретением и ломала штатный случай.
    # Все отправленные строки даты участвуют в сверке ниже — какое именно ушло, читается по дате,
    # а состав и хеши каждой отправки лежат в sent-artifacts.tsv.
    audit = toolkit.area(root, "audit")
    files = [f for f in os.listdir(audit) if f.endswith(".md") and not f.startswith("audit-package-")]
    for fn in sorted(files):
        own_date = (re.search(r"(\d{4}-\d{2}-\d{2})", fn) or [None, ""])[1] if re.search(r"(\d{4}-\d{2}-\d{2})", fn) else ""
        text = read(os.path.join(audit, fn))
        # Шапка письма — метаданные пакета, а не проза о прошлом: строка `snapshot:` цитирует состояние
        # пакета (в том числе прошлую дату и её числа), и читать её как утверждение о прошлом артефакте
        # значит ругаться на собственную бухгалтерию. Нашла аттестация: безвредные канарейки №54 и №64
        # (заполненный заголовок ответа, сходящийся прирост) давали ложные срабатывания §27.
        _fm = re.match(r"(?s)^---\r?\n.*?\r?\n---\r?\n", text)
        if _fm:
            text = text[_fm.end():]
        for m in re.finditer(r"[^\n.]{0,120}?пакет\w*[^\n.]{0,40}?(\d{4}-\d{2}-\d{2})[^\n.]{0,160}?(\d{1,3})\s*([а-яё]+)", text):
            date, num, word = m.group(1), m.group(2), m.group(3).lower()
            if not own_date or date >= own_date:
                continue          # свой собственный снапшот — не утверждение о прошлом пакете
            metric = next((k for pat, k in METRIC_PATTERNS if re.match(pat, word)), None)
            if not metric:
                continue
            rows = by_date.get(date)
            if not rows:
                problems.append(f"{fn}: утверждение о пакете {date} ({num} {word}) не проверяемо — нет слепка")
                continue
            known = {r.get(metric) for r in rows}
            if num not in known:
                problems.append(f"{fn}: «{num} {word}» против слепка {date}: {metric} = {', '.join(sorted(x for x in known if x))}")
        # утверждение о прошлом без даты: проверить нечем — это тоже дефект, а не мелочь.
        # Номер пакета + любое число в предложении = утверждение о содержании прошлого артефакта.
        for sent in re.split(r"(?<=[.!?])\s+", text):
            m_ord = re.search(r"\b(перв|втор|трет|четвёрт|четверт|пят|шест|седьм)\w*\s+пакет\w*", sent, re.IGNORECASE)
            if not m_ord:
                continue
            m_num = re.search(r"\b(\d{1,3})\b", sent.replace("2026", "").replace("2025", ""))
            if not m_num:
                continue
            if re.search(r"\b(\d{4}-\d{2}-\d{2})\b", sent):
                continue          # датированное утверждение проверяется выше по слепку
            problems.append(f"{fn}: «{m_ord.group(0)}» с числом {m_num.group(1)} без даты — не сверяется со слепком")

    if not quiet:
        if problems:
            print("утверждения о прошлом, не подтверждённые слепками:")
            for p in problems:
                print("  -", p)
        else:
            print(f"утверждения о прошлом: подтверждено слепками ({len(by_date)} дат в реестре)")
    return 1 if problems else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=("archive", "check", "mark-sent"))
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--package")
    ap.add_argument("--date")
    ap.add_argument("--kind", default="manual", choices=["manual", "auto", "sent"],
                        help="вид слепка: sent ставит только путь отправки (tasks.py send)")
    ap.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "archive":
        if not a.package:
            sys.exit("нужен --package")
        sys.exit(cmd_archive(a.wiki, a.package, a.date, a.note, kind=a.kind))
    if a.cmd == "mark-sent":
        sys.exit(cmd_mark_sent(a.wiki, a.date))
    sys.exit(cmd_check(a.wiki))



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    main()
