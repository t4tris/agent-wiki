#!/usr/bin/env python3
"""Поиск секретов в ИСТОРИИ репозитория (не в файлах — за это отвечает линтер §35).

Зачем отдельный инструмент: 2026-09-14 обнаружилось, что ключ вида `sk-…` (51 знак) лежит не только
в файлах, откуда его вычистили, но и в блобах старых коммитов. Проверка по файлам такое не ловит
по определению, и пока инструмента не было, утверждение «ключ вырезан» было непроверяемым.

Как работает: берёт ВСЕ объекты репозитория (включая недостижимые), отбирает блобы и прогоняет их
одним потоком через `git cat-file --batch` — это секунды на 3 000 блобов, а не минуты по одному
процессу на объект. Образец намеренно узкий (`sk-` + 25 и более буквенно-цифровых знаков), иначе
в находки попадают обычные слова вроде «task-», «disk-», «risk-»: первый прогон так и дал 460 ложных.

Возврат: 0 — чисто; 2 — найдены блобы с образцом (тогда ищут и переписывают историю, а не файлы).

    python3 _toolkit/scan_history_secrets.py --wiki . [--pattern 'sk-[A-Za-z0-9]{25,}']
"""
import argparse
import os
import re
import subprocess
import sys


def _text(value):
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    return value or ""


def _result_text(result):
    parts = [_text(result.stdout).strip(), _text(result.stderr).strip()]
    return "\n".join(part for part in parts if part)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--pattern", default=r"\bsk-[A-Za-z0-9]{25,}\b")
    ap.add_argument("--show-paths", action="store_true", help="сопоставить найденные блобы с путями (дороже)")
    a = ap.parse_args()
    root = a.wiki
    if not os.path.exists(os.path.join(root, ".git")):
        print("нет рабочего дерева git — историю проверять нечем")
        return 0
    pat = re.compile(a.pattern.encode())

    try:
        r = subprocess.run(["git", "-C", root, "cat-file", "--batch-all-objects",
                            "--batch-check=%(objecttype) %(objectname)"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
    except OSError as exc:
        print("СБОЙ git cat-file --batch-all-objects: %s" % exc, file=sys.stderr)
        return 1
    if r.returncode != 0:
        print("СБОЙ git cat-file --batch-all-objects: %s" % (_result_text(r) or "код возврата %d" % r.returncode), file=sys.stderr)
        return 1
    shas = []
    for line in (r.stdout or "").splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) != 2 or parts[0] not in {"blob", "tree", "commit", "tag"}:
            print("СБОЙ git cat-file --batch-all-objects: неожиданная строка %r" % line, file=sys.stderr)
            return 1
        if parts[0] == "blob":
            shas.append(parts[1])
    if not shas:
        print("в репозитории нет блобов")
        return 0

    try:
        p = subprocess.Popen(["git", "-C", root, "cat-file", "--batch"],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = p.communicate(("\n".join(shas) + "\n").encode())
    except OSError as exc:
        print("СБОЙ git cat-file --batch: %s" % exc, file=sys.stderr)
        return 1
    if p.returncode != 0:
        detail = _text(err).strip()
        print("СБОЙ git cat-file --batch: %s" % (detail or "код возврата %d" % p.returncode), file=sys.stderr)
        return 1
    if isinstance(out, str):
        out = out.encode()
    out = out or b""
    hits, pos = [], 0
    for expected in shas:
        nl = out.find(b"\n", pos)
        if nl < 0:
            print("СБОЙ git cat-file --batch: вывод оборван на объекте %s" % expected, file=sys.stderr)
            return 1
        head = out[pos:nl].split()
        if len(head) != 3 or _text(head[0]) != expected or head[1] != b"blob":
            print("СБОЙ git cat-file --batch: неожиданный ответ для %s" % expected, file=sys.stderr)
            return 1
        try:
            size = int(head[2])
        except ValueError:
            print("СБОЙ git cat-file --batch: неверный размер объекта %s" % expected, file=sys.stderr)
            return 1
        body_start = nl + 1
        body_end = body_start + size
        if body_end >= len(out) or out[body_end:body_end + 1] != b"\n":
            print("СБОЙ git cat-file --batch: неполное тело объекта %s" % expected, file=sys.stderr)
            return 1
        body = out[body_start:body_end]
        if pat.search(body):
            hits.append(expected)
        pos = body_end + 1
    if pos != len(out):
        print("СБОЙ git cat-file --batch: после объектов остались данные", file=sys.stderr)
        return 1

    print(f"блобов проверено: {len(shas)} | с образцом секрета: {len(hits)}")
    if hits and a.show_paths:
        try:
            rv = subprocess.run(["git", "-C", root, "rev-list", "--objects", "--all"],
                                capture_output=True, text=True, encoding="utf-8", errors="replace", check=False)
        except OSError as exc:
            print("СБОЙ git rev-list: %s" % exc, file=sys.stderr)
            return 1
        if rv.returncode != 0:
            print("СБОЙ git rev-list: %s" % (_result_text(rv) or "код возврата %d" % rv.returncode), file=sys.stderr)
            return 1
        path_of = {}
        for line in (rv.stdout or "").splitlines():
            if " " in line:
                s, path = line.split(" ", 1)
                path_of.setdefault(s, path)
        for h in hits[:20]:
            print(f"  {h[:10]} {path_of.get(h, '(имя не найдено: объект недостижим или удалён)')}")
    elif hits:
        for h in hits[:10]:
            print(f"  {h[:10]}")
    if hits:
        print("НЕ ЧИСТО: образец секрета найден в истории. Файлы чистить бесполезно — нужна перепись истории.")
        return 2
    print("чисто: образца секрета в истории нет")
    return 0


if __name__ == "__main__":
    sys.exit(main())
