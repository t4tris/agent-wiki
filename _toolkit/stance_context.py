#!/usr/bin/env python3
"""Свод тезисов вики для смыслового прохода: одна страница — один раздел, ни одна не пропущена.

Зачем. Смысловой проход («вся вика в контексте, найди расхождения по смыслу») требует, чтобы в контекст
ребёнка попала КАЖДАЯ страница. Обходить 67 файлов вручную — 67 чтений и молчаливые пропуски: то, что
пропущено, не видно ни в отчёте, ни в проверке. Поэтому обход делает эта команда: она идёт по дереву
`wiki/` файловой системой (рельсы), собирает свод и СРАЗУ проверяет полноту — каждая страница ровно один
раз, ничего лишнего, ничего забытого. Не сошлось — команда падает, а не собирает неполный свод.

Что попадает в свод (страница целиком не влезает и не нужна — нужны ТЕЗИСЫ):

* заголовок страницы и её `summary`;
* все заголовки разделов (по ним видно, что страница вообще утверждает);
* разделы «Спорное…», «Факты и цифры», «Числа и замеры» текстом — там живут утверждения, с которыми
  можно спорить.

Точные цитаты ребёнок берёт потом из самих страниц: свод даёт карту тезисов, а не замену источнику.

    python3 _toolkit/stance_context.py --wiki .                 # проверить полноту и показать размер
    python3 _toolkit/stance_context.py --wiki . --write         # записать свод и нарезку для чтения
"""
import argparse
import glob
import io
import os
import re
import sys

import toolkit

THESIS_SECTIONS = re.compile(r"(?ms)^## (?:Спорное[^\n]*|Факты и цифры|Числа и замеры)\s*$(.*?)(?=^## |\Z)")
CHUNK = 90_000            # знаков на часть: одно чтение файла держит ~100 тыс., берём с запасом


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def page_files(root):
    """Рельсы: все страницы вики, кроме зеркала источников и служебных файлов."""
    vault = toolkit.wiki(root)
    out = []
    for p in sorted(glob.glob(toolkit.wiki(root, "**", "*.md"), recursive=True)):
        rel = os.path.relpath(p, root).replace("\\", "/")
        if "/sources/" in rel or "/_meta/" in rel:
            continue
        out.append(rel)
    return out


def digest_of(root, rel):
    text = read(os.path.join(root, rel))
    body = re.sub(r"(?ms)^---\n.*?\n---\n", "", text, count=1)
    title = re.search(r"(?m)^title:\s*(.+)$", text)
    summary = re.search(r"(?m)^summary:\s*(.+)$", text)
    heads = [l for l in body.split("\n") if l.startswith("#")]
    sections = ["\n".join(m.group(1).split("\n")[1:]).strip()[:4000] for m in THESIS_SECTIONS.finditer(body)]
    lines = ["## " + rel]
    if title:
        lines.append("title: " + title.group(1).strip())
    if summary:
        lines.append("summary: " + summary.group(1).strip())
    lines.append("Тезисы (заголовки): " + " | ".join(h.lstrip("# ").strip() for h in heads))
    for s in sections:
        if s:
            lines.append("Спорное/факты:\n" + s)
    return "\n".join(lines) + "\n\n"


def build(root):
    files = page_files(root)
    vault = toolkit.wiki(root)
    if not os.path.isdir(vault):
        # Хранилища нет вовсе — это сломанный экземпляр, а не пустой: молчать нельзя, иначе «свод собран»
        # окажется ложью (канарейка рельсов 174).
        sys.exit(f"нет каталога хранилища {vault}: собирать свод не из чего")
    if not files:
        # Пустое хранилище — не ошибка, а состояние свежего экземпляра: собирать нечего. Выход с нулём,
        # иначе первый запуск останавливается на этом шаге и не строит остального (находка пробы 2026-09-22).
        print("страниц в вики нет: свод собирать нечего (" + root + ")")
        return None, [], []
        return None
    parts, chunks, cur = [], [], ["# Свод тезисов вики (смысловой проход: что утверждает каждая страница)", ""]
    for rel in files:
        block = digest_of(root, rel)
        parts.append(block)
        if sum(len(x) for x in cur) + len(block) > CHUNK:
            chunks.append("".join(cur))
            cur = ["# Свод тезисов вики (продолжение)", ""]
        cur.append(block)
    chunks.append("".join(cur))
    text = "".join(parts)
    # Полнота: каждая страница ровно один раз. Это и есть «рельсы» — пропуск виден сразу.
    # Имена страниц входящего потока владельца содержат пробелы (wiki/Clippings/…), а `\S+` их не ловил:
    # полнота свода не сходилась, и `--write` отказывался писать вовсе — метод «свод тезисов плюс источник
    # целиком» был недоступен молча. Нашёл круг правки на детях 2026-09-21.
    seen = re.findall(r"(?m)^## (wiki/.+\.md)$", text)
    if sorted(seen) != sorted(files):
        missing = sorted(set(files) - set(seen))
        extra = sorted(set(seen) - set(files))
        dups = sorted({x for x in seen if seen.count(x) > 1})
        sys.exit("свод неполон: пропущено %s, лишнее %s, дубли %s" % (missing, extra, dups))
    return text, chunks, files


def main():
    ap = argparse.ArgumentParser(description="свод тезисов вики для смыслового прохода")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.wiki)
    text, chunks, files = build(root)
    if text is None:
        return 0                      # страниц нет: свода не будет, и это не сбой шага
    print("страниц в своде: " + str(len(files)) + " (каждая ровно один раз — проверено)")
    print("размер свода: " + str(len(text)) + " знаков ≈ " + str(len(text) // 4) + " токенов")
    print("частей для чтения: " + str(len(chunks)) + " по " + str(CHUNK // 1000) + " тыс. знаков")
    if args.write:
        base = toolkit.area(root, "stance")
        os.makedirs(base, exist_ok=True)
        with io.open(os.path.join(base, "_wiki-theses.md"), "w", encoding="utf-8", newline="") as f:
            f.write(text)
        d = os.path.join(base, "theses-parts")
        for old in glob.glob(os.path.join(d, "*.md")):
            os.remove(old)
        os.makedirs(d, exist_ok=True)
        for i, chunk in enumerate(chunks, 1):
            with io.open(os.path.join(d, "part-%d.md" % i), "w", encoding="utf-8", newline="") as f:
                f.write(chunk)
        print("записано: _staging/stance/_wiki-theses.md и _staging/stance/theses-parts/part-*.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
