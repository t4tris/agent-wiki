#!/usr/bin/env python3
"""Дежурство стража цитат: `verify` реестра в обе стороны на временной песочнице.

Чистый черновик обязан пройти, черновик с выдуманным id — упасть. Хвостовой
`--wiki` от единой точки входа терпим и игнорируем: у стража свой реестр.
Отчёт — парой (что сошлось, что нет), а не вердиктом.
"""
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCES = os.path.join(HERE, "citation_ledger", "sources.py")


def run(ledger, *args):
    p = subprocess.run([sys.executable, "-X", "utf8", SOURCES, "--ledger", ledger, *args],
                       capture_output=True, text=True, errors="replace", check=False)
    return p.returncode, (p.stdout + p.stderr).strip()


def main():
    tmp = tempfile.mkdtemp(prefix="cite-duty-")
    ledger = os.path.join(tmp, "ledger.json")
    clean = os.path.join(tmp, "clean.md")
    dirty = os.path.join(tmp, "dirty.md")

    rc, out = run(ledger, "reset")
    if rc != 0:
        print(f"реестр не сброшен: {out}")
        return 1
    rc, out = run(ledger, "add", "https://example.com/sky")
    if rc != 0 or "[1]" not in out:
        print(f"источник не встал в реестр: {out}")
        return 1

    with open(clean, "w", encoding="utf-8") as f:
        f.write("The sky is blue [1].\n")
    rc, block = run(ledger, "render", "--cited-in", clean)
    if rc != 0 or "[1]" not in block:
        print(f"блок Sources не собран: {block}")
        return 1
    with open(clean, "a", encoding="utf-8") as f:
        f.write("\n" + block + "\n")
    with open(dirty, "w", encoding="utf-8") as f:
        f.write("The sky is green [99].\n\n" + block + "\n")

    rc_clean, out_clean = run(ledger, "verify", clean)
    rc_dirty, out_dirty = run(ledger, "verify", dirty)

    print(f"чистый черновик: {'прошёл' if rc_clean == 0 else 'УПАЛ: ' + out_clean}")
    print(f"выдуманный id: {'пойман' if rc_dirty != 0 else 'ПРОПУЩЕН — страж слеп'}")
    if rc_clean == 0 and rc_dirty != 0:
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
