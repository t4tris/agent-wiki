#!/usr/bin/env python3
"""Отчёт по тесту свода стиля: что исправили дети, красным.

    python3 _toolkit/style_test_report.py --wiki . [--base 701f5e5]

Собирает разницу рабочего дерева с базовым коммитом по страницам вики и рисует HTML: удалённые строки —
серым с зачёркиванием, добавленные (то, что поправили дети) — красным, контекст — обычным. Отчёт нужен для
глазного приёма: владелец смотрит на красное и решает, принимать свод стиля или откатывать статьи.

Тема тёмная: так владелец читает артефакты вики.
"""
import argparse
import difflib
import html
import io
import os
import subprocess

import toolkit


def git(root, *args):
    return subprocess.run(["git", "-C", root] + list(args), capture_output=True, text=True,
                          encoding="utf-8", errors="replace", check=True).stdout


def read(path):
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def page_html(rel, old, new):
    out = ["<h2>%s</h2>" % html.escape(rel)]
    adds = sum(1 for l in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0) if l.startswith("+") and not l.startswith("+++"))
    dels = sum(1 for l in difflib.unified_diff(old.splitlines(), new.splitlines(), n=0) if l.startswith("-") and not l.startswith("---"))
    out.append('<p class="stat">добавлено строк: <b>%d</b>, убрано строк: <b>%d</b></p>' % (adds, dels))
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), lineterm="", n=3))
    if not diff:
        out.append('<p class="same">изменений нет</p>')
        return "\n".join(out), adds, dels
    out.append("<pre>")
    for line in diff:
        if line.startswith("+++") or line.startswith("---") or line.startswith("@@"):
            out.append('<span class="hunk">%s</span>' % html.escape(line))
        elif line.startswith("+"):
            out.append('<span class="add">%s</span>' % html.escape(line))
        elif line.startswith("-"):
            out.append('<span class="del">%s</span>' % html.escape(line))
        else:
            out.append(html.escape(line))
    out.append("</pre>")
    return "\n".join(out), adds, dels


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--base", default=None)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    root = a.wiki
    base = a.base or "HEAD"
    changed = [l for l in git(root, "diff", "--name-only", base, "--", "wiki").splitlines() if l.endswith(".md")]
    changed = [l for l in changed if "/sources/" not in l and not l.endswith("index.md")]
    parts, total_add, total_del = [], 0, 0
    for rel in sorted(changed):
        old = git(root, "show", "%s:%s" % (base, rel))
        path = os.path.join(root, *rel.split("/"))
        new = read(path) if os.path.exists(path) else ""
        block, adds, dels = page_html(rel, old, new)
        total_add += adds
        total_del += dels
        parts.append(block)
    head = """<!doctype html><html lang="ru"><head><meta charset="utf-8">
<title>Тест свода стиля: что поправили дети</title>
<style>
 body{background:#14161a;color:#e6e6e6;font:15px/1.55 Georgia,serif;margin:0;padding:32px 40px;max-width:1100px}
 h1{font-size:26px;margin:0 0 6px} h2{font-size:19px;margin:34px 0 6px;border-bottom:1px solid #333;padding-bottom:4px}
 .stat{color:#9aa0a6;margin:4px 0 10px;font:13px/1.4 Consolas,monospace}
 .same{color:#7fbf7f}
 pre{background:#1b1e23;border:1px solid #2a2e35;border-radius:6px;padding:12px 14px;overflow-x:auto;
      font:13px/1.5 Consolas,"Courier New",monospace;white-space:pre-wrap}
 .add{color:#ff5f56;background:rgba(255,95,86,.08);display:block}
 .del{color:#6b7280;text-decoration:line-through;display:block}
 .hunk{color:#7aa2f7;display:block}
 .legend{margin:18px 0 0;padding:12px 14px;background:#1b1e23;border:1px solid #2a2e35;border-radius:6px;font:13px/1.5 Consolas,monospace}
 .rollback{margin-top:10px;color:#ffb86c;font:13px/1.5 Consolas,monospace}
</style></head><body>
"""
    head += "<h1>Тест свода стиля: что поправили дети</h1>\n"
    head += '<p class="stat">страниц изменено: <b>%d</b> | добавлено строк: <b>%d</b> | убрано строк: <b>%d</b> | база для отката: <b>%s</b></p>' % (len(changed), total_add, total_del, html.escape(base))
    head += ('<div class="legend">красным — то, что дети добавили или переписали (это и есть исправления по своду стиля); '
             'серым с зачёркиванием — что убрано; синим — границы правок.</div>')
    head += '<div class="rollback">Откат всех правок: git -C . checkout %s -- wiki</div>' % html.escape(base)
    if not parts:
        parts.append("<h2>Правок нет</h2><p class=\"same\">Рабочее дерево совпадает с базой: дети ничего не изменили.</p>")
    doc = head + "\n".join(parts) + "\n</body></html>\n"
    out = a.out or toolkit.area(root, "audit", "style-test-2026-09-19.html")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with io.open(out, "w", encoding="utf-8", newline="") as f:
        f.write(doc)
    print("отчёт: %s | страниц %d | +%d/−%d" % (out, len(changed), total_add, total_del))


if __name__ == "__main__":
    main()
