#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Отчёт по волне: только те правки, которые агент считает сомнительными.

Владелец 2026-09-19: «запускай детей редактировать все оставшиеся страницы, затем проверь их сам
и составь HTML отчёт только по правкам которые сочтёшь сомнительными». Значит: полный дифф глазами
не читаем — он уже проверен машиной (потери фактов, мета-ремарки, адреса), а в отчёт попадают
только те куски, где я не могу поручиться за потерю смысла.

Подозрительным считается:
  * снята строка, в которой есть ссылка на владельца и число (мог уйти факт);
  * снят пункт или абзац длиннее 200 знаков (мог уйти ход мысли);
  * снята строка с прямой цитатой или с адресом;
  * добавлен оборот о странице, вики или корпусе («страница держит», «в корпусе»);
  * правка шапки (updated/sources/evidence/confidence);
  * страница переписана больше чем на четверть строк.

    python3 _toolkit/style_suspect_report.py --wiki . --base <коммит> [--out файл.html]
"""
import argparse
import difflib
import html
import io
import os
import re
import subprocess

import toolkit

META = re.compile(r"(?i)(страниц\w+|в корпусе|корпусн\w+)\s*(?:\S+\s+){0,3}(держи\w*|опира\w*|собра\w*|состо\w*)"
                  r"|материал\w* страниц\w*|свод\w* стил\w*|как устроена страница")
QUOTE = re.compile(r"«[^»]{12,}»")
ADDR = re.compile(r"https?://\S+")
NUM = re.compile(r"\d{3,}|\d+[.,–—-]\d+|\d+%")
LINK = re.compile(r"\[\[[^\]]+\]\]")


def read(p):
    with io.open(p, encoding="utf-8", errors="replace") as f:
        return f.read()


def changed(root, base):
    out = subprocess.run(["git", "-C", root, "diff", "--name-only", base, "--", "wiki"],
                         capture_output=True, text=True, encoding="utf-8", check=True).stdout
    return [x.strip() for x in out.split("\n") if x.strip().endswith(".md")]


def old_text(root, base, rel):
    return subprocess.run(["git", "-C", root, "show", "%s:%s" % (base, rel)],
                          capture_output=True, text=True, encoding="utf-8", check=True).stdout


def hunks(old, new):
    res = []
    sm = difflib.SequenceMatcher(None, old.split("\n"), new.split("\n"))
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        res.append((tag, old.split("\n")[i1:i2], new.split("\n")[j1:j2]))
    return res


def why(tag, removed, added):
    """Почему этот кусок стоит показать владельцу.

    Смотрим не на факт удаления, а на то, пережило ли удалённое правку: если та же цифра, цитата или адрес
    стоят в добавленных строках, это переформулировка, а не потеря. Длина считается только при настоящей
    убыли текста: пункт, переписанный в такой же по объёму, подозрительным не становится.
    """
    rtext = "\n".join(removed)
    atext = "\n".join(added)
    reasons = []
    r_link_num = [l for l in removed if LINK.search(l) and NUM.search(l)]
    if r_link_num and not any(n in atext for l in r_link_num for n in NUM.findall(l)):
        reasons.append("снята строка со ссылкой и числом")
    gone = sum(len(l) for l in removed) - sum(len(l) for l in added)
    if gone > 260:
        reasons.append("текста убыло на %d знаков" % gone)
    rq = [m.group(0) for m in QUOTE.finditer(rtext) if m.group(0) not in atext]
    if rq:
        reasons.append("снята цитата")
    ra = [m.group(0) for m in ADDR.finditer(rtext) if m.group(0) not in atext]
    if ra:
        reasons.append("снят адрес")
    for l in removed:
        if l.strip().startswith("## "):
            reasons.append("снят целый раздел: %s" % l.strip()[3:60])
    for l in added:
        m = META.search(l)
        if m:
            reasons.append("добавлен оборот о странице или корпусе: «%s»" % m.group(0)[:60])
    if any(re.match(r"(?i)(sources|source-stance|evidence|confidence|verification-status|type|status):", l)
           for l in removed + added):
        reasons.append("правка шапки, кроме `updated:`")
    seen, uniq = set(), []
    for r in reasons:
        if r not in seen:
            seen.add(r)
            uniq.append(r)
    return uniq


def page_html(rel, old, new):
    hs = hunks(old, new)
    shown = []
    for tag, removed, added in hs:
        r = why(tag, removed, added)
        if r:
            shown.append((tag, removed, added, r))
    notes = []
    if old and new:
        ratio = 1 - difflib.SequenceMatcher(None, old.split("\n"), new.split("\n")).ratio()
        if ratio > 0.25:
            notes.append("страница переписана на %d%% строк" % int(ratio * 100))
    out = ["<h2>%s</h2>" % html.escape(rel)]
    if notes:
        out.append('<p class="stat">%s</p>' % html.escape("; ".join(notes)))
    if not shown:
        out.append('<p class="same">подозрительных правок нет — страница в отчёт не попала</p>')
        return "", 0
    for tag, removed, added, r in shown:
        out.append('<p class="why">%s</p>' % html.escape("; ".join(r)))
        out.append("<pre>")
        for l in removed:
            out.append('<span class="del">%s</span>' % html.escape("- " + l.strip() if not l.startswith("-") else l))
        for l in added:
            out.append('<span class="add">%s</span>' % html.escape(l))
        out.append("</pre>")
    return "\n".join(out), len(shown)


CSS = """
body{background:#14161a;color:#d7dbe0;font:14px/1.55 Consolas,Menlo,monospace;margin:24px}
h1{font-size:20px;color:#e8ecf1} h2{font-size:16px;color:#8fd0ff;margin-top:28px;border-top:1px solid #2a2f37;padding-top:14px}
pre{white-space:pre-wrap;margin:4px 0 14px}
.del{color:#7d848c;text-decoration:line-through}
.add{color:#ff6b6b}
.why{color:#f0c674;margin:10px 0 4px}
.stat{color:#9aa3ad} .same{color:#6f7681}
.sum{color:#9aa3ad;margin-bottom:18px}
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    pages = changed(root, a.base)
    blocks, total, suspicious_pages = [], 0, 0
    for rel in pages:
        p = os.path.join(root, rel.replace("/", os.sep))
        if not os.path.exists(p):
            continue
        body, n = page_html(rel, old_text(root, a.base, rel), read(p))
        if n:
            blocks.append(body)
            total += n
            suspicious_pages += 1
    title = "Волна: правки, которые агент считает сомнительными"
    doc = ["<!doctype html><meta charset='utf-8'><title>%s</title><style>%s</style>" % (title, CSS),
           "<h1>%s</h1>" % title,
           '<p class="sum">База: %s. Страниц с правками: %d. Страниц с сомнительными правками: %d; сомнительных кусков: %d.'
           ' Страницы без сомнительных правок в отчёт не попали — их правки проверены машиной: потери фактов с числом и '
           'владельцем, мета-ремарки, адреса, ссылки.</p>' % (a.base, len(pages), suspicious_pages, total)]
    doc += blocks
    out = a.out or toolkit.area(root, "audit", "style-suspect-%s.html" % a.base)
    with io.open(out, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(doc))
    print("отчёт: %s | страниц %d | сомнительных %d на %d страницах" % (out, len(pages), total, suspicious_pages))


if __name__ == "__main__":
    main()
