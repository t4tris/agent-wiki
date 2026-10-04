#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Метрика пересказа: сколько две служебные документы говорят одно и то же.

Зачем. Перед тем как ставить порог сторожу «документ не пересказывает соседа», нужен замер, которому можно
верить дважды: рецепт задан командой, а не описанием, и видно, что именно пересеклось — проза, таблица или
машинерия в код-фенсах. Повтор в фенсах и строках таблиц часто законен (одна и та же команда, один и тот же
шаблон строки), поэтому на решение идёт только проза.

Рецепт (закреплён здесь, менять только вместе с порогом у сторожа):
  1. Текст документа делится на три рода построчно: фенс (между ```), таблица (строка начинается с `|`),
     остальное — проза.
  2. Из каждой части снимается разметка: символы `*`, `#`, `>`, `|`, `-`, обратные кавычки заменяются пробелом.
  3. Текст приводится к словам: последовательности русских/латинских букв, цифр и кавычек-ёлочек.
  4. Считаются восьмисловные последовательности в нижнем регистре; совпадением считается точное равенство
     последовательностей. Длинной считается фраза длиннее 45 знаков.
  5. Пересечение пары — по каждому роду отдельно. В очередь попадают пары, у которых есть длинные совпадения
     в ПРОЗЕ; совпадения в таблицах и фенсах печатаются рядом как справка.

Запуск:
  python3 _toolkit/retell_metric.py --wiki .                 # очередь по служебным документам
  python3 _toolkit/retell_metric.py --wiki . --all-pairs     # все пары с любым пересечением
  python3 _toolkit/retell_metric.py --self-test              # канарейка самой метрики
"""

import argparse
import datetime
import io
import itertools
import json
import os
import re
import subprocess
import sys

import toolkit

NGRAM = 8
LONG = 45
WORD = re.compile(r"[А-Яа-яЁёA-Za-z0-9«»]+")
MARKUP = re.compile(r"[`*#>|\-]")


def split_kinds(text):
    """Три рода: проза, таблица, фенс. Строка принадлежит ровно одному роду."""
    prose, table, fence = [], [], []
    inside = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            inside = not inside
            continue
        if inside:
            fence.append(line)
        elif line.lstrip().startswith("|"):
            table.append(line)
        else:
            prose.append(line)
    return "\n".join(prose), "\n".join(table), "\n".join(fence)


def grams(text, n=NGRAM):
    words = WORD.findall(MARKUP.sub(" ", text))
    return {" ".join(words[i:i + n]).lower() for i in range(max(0, len(words) - n + 1))}


def lines_grams(text, least=3):
    """Строки, а не восьмисловные фразы: в фенсах и таблицах фраза короче восьми слов.

    Восьмисловное окно по коду и строкам таблиц не даёт ничего (команда из трёх-четырёх слов), поэтому там
    сравниваются целые строки после снятия разметки — иначе «машинерия» в разбивке всегда ноль, и вопрос
    «что именно пересеклось» остаётся без ответа (замечание второй сессии 2026-09-22).
    """
    out = set()
    for line in text.split("\n"):
        words = WORD.findall(MARKUP.sub(" ", line))
        if len(words) >= least:
            out.add(" ".join(words).lower())
    return out


def kinds_of(path):
    text = io.open(path, encoding="utf-8", errors="replace").read()
    p, t, f = split_kinds(text)
    return {"проза": grams(p), "таблица": lines_grams(t), "фенс": lines_grams(f, 2)}


def generated_names(root):
    """Какие документы генерируемые — спрашиваем у сторожа, а не держим свой список.

    «Генерируемые вне очереди пересказа» — решение про очередь, а не про наблюдение: под §87 эти документы
    остаются (там другое решение — править шаблон). Поэтому предикат берётся из линтера, где он один на весь
    проект, иначе два места разойдутся (замечание владельца 2026-09-22).
    """
    try:
        sys.path.insert(0, toolkit.area(root))
        import lint_wiki
        return lambda name: bool(lint_wiki.REF_GENERATED.match(name))
    except (ImportError, OSError) as ex:
        print(f"предупреждение: список генерируемых документов недоступен ({ex})", file=sys.stderr)
        return lambda name: False


def service_docs(root):
    """Тот же набор, что у сторожа: корневые документы плюс документы `_staging/` первого уровня."""
    docs = []
    for name in sorted(os.listdir(root)):
        if name.endswith(".md") and name not in ("log.md",):
            docs.append(os.path.join(root, name))
    stage = toolkit.area(root)
    if os.path.isdir(stage):
        for name in sorted(os.listdir(stage)):
            if name.endswith(".md"):
                docs.append(os.path.join(stage, name))
    for extra in ("_staging/ingest-input/README.md", "_staging/audit/README.md",
                  "_tools/tg-saved/README.md", "fixture/README.md"):
        p = os.path.join(root, extra)
        if os.path.exists(p):
            docs.append(p)
    return docs


def measure(root, all_pairs=False):
    docs = service_docs(root)
    is_generated = generated_names(root)
    docs = [p for p in docs if not is_generated(os.path.basename(p))]
    K = {os.path.relpath(p, root).replace("\\", "/"): kinds_of(p) for p in docs}
    rows = []
    for a, b in itertools.combinations(sorted(K), 2):
        per = {}
        for kind in ("проза", "таблица", "фенс"):
            common = K[a][kind] & K[b][kind]
            long_ = [g for g in common if len(g) >= LONG]
            per[kind] = (len(common), len(long_))
        if any(v[0] for v in per.values()):
            rows.append({"a": a, "b": b, "per": per})
    rows.sort(key=lambda r: (-r["per"]["проза"][1], -r["per"]["проза"][0]))
    if not all_pairs:
        rows = [r for r in rows if r["per"]["проза"][1] > 0]
    return K, rows


def tree_state(root):
    """Ревизия и состояние дерева на момент прогона.

    Замер идёт по рабочему дереву, а оно может отличаться от HEAD: тогда «ревизия» без пометки о грязном
    дереве описывает не то, что измерено, и воспроизвести замер по его же полям нельзя. Поэтому в сводку
    идут и ревизия, и признак грязного дерева, и хеши самих измеренных документов — по ним замер сверяется
    даже тогда, когда правки ещё не закоммичены (находка владельца 2026-09-22).
    """
    rev_proc = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"],
                              capture_output=True, text=True, encoding="utf-8", check=False)
    rev = rev_proc.stdout.strip() if rev_proc.returncode == 0 else "—"
    dirty_proc = subprocess.run(["git", "-C", root, "status", "--porcelain"],
                                capture_output=True, text=True, encoding="utf-8", check=False)
    dirty = bool(dirty_proc.stdout.strip()) if dirty_proc.returncode == 0 else None
    return rev, dirty


def doc_hashes(root, names):
    import hashlib
    out = {}
    for name in names:
        path = os.path.join(root, name)
        if os.path.exists(path):
            out[name] = hashlib.sha256(io.open(path, "rb").read()).hexdigest()[:16]
    return out


def report(root, rows, K):
    rev, dirty = tree_state(root)
    day = datetime.date.today().isoformat()
    state = ("состояние git неизвестно" if dirty is None else
             "дерево грязное (замер по рабочему дереву, не по ревизии)" if dirty else "дерево чистое")
    print(f"документов: {len(K)} | пар: {len(K) * (len(K) - 1) // 2} | ревизия {rev} | {day} | {state}")
    print("рецепт: фенсы, таблицы и проза раздельно; разметка снята; восьмисловные фразы; длинная — от 45 знаков; таблицы и фенсы — совпадение целых строк")
    print("")
    print(f"{'проза':>13} | {'таблица':>13} | {'фенс':>13} | пара")
    for r in rows:
        p, t, f = r["per"]["проза"], r["per"]["таблица"], r["per"]["фенс"]
        print(f"{p[0]:>4} ({p[1]:>2} длин.) | {t[0]:>4} ({t[1]:>2} длин.) | {f[0]:>4} ({f[1]:>2} длин.) | {r['a']} ↔ {r['b']}")
    if not rows:
        print("пар с длинными совпадениями в прозе нет — очередь пуста")
    out = toolkit.area(root, "audit", f"retell-{day}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    io.open(out, "w", encoding="utf-8", newline="").write(json.dumps(
        {"day": day, "rev": rev, "tree_dirty": dirty, "docs_sha": doc_hashes(root, sorted(K)),
         "docs": sorted(K), "ngram": NGRAM, "long": LONG,
         "recipe": "проза — 8-словные фразы; таблицы и фенсы — совпадение целых строк; разметка снята; длинная от 45 знаков",
         "note": "rev — ревизия HEAD на момент прогона; при tree_dirty=true замер описывает рабочее дерево, сверять по docs_sha",
         "queue": rows}, ensure_ascii=False, indent=1))
    print(f"\nмашинная сводка: {out}")
    return 0


def self_test():
    """Канарейка метрики: фиксированный рецепт на подложенной паре.

    Три рода проверяются раздельно — иначе метрика, склеившая фенс с прозой, покажет «пересказ» там, где
    повторяется команда, и попросит вычистить то, что должно повторяться.
    """
    tmp = os.path.join(os.environ.get("LOCALAPPDATA", "/tmp"), "Temp", "retell-selftest")
    os.makedirs(tmp, exist_ok=True)
    shared = "документ обязан держать требование а не рассказывать историю своего появления в проекте"
    a = os.path.join(tmp, "a.md")
    b = os.path.join(tmp, "b.md")
    row = "| источник лежит в мастере | копия лежит в хранилище для чтения |"
    code = "```\npython3 _toolkit/lint_wiki.py --wiki . --no-summary\n```"
    io.open(a, "w", encoding="utf-8").write(shared + "\n\n" + row + "\n\n" + code + "\n")
    io.open(b, "w", encoding="utf-8").write(shared + "\n\n" + row + "\n\n" + code + "\n")
    K = {"a.md": kinds_of(a), "b.md": kinds_of(b)}
    bad = []
    for kind, least in (("проза", 3), ("таблица", 1), ("фенс", 1)):
        n = len(K["a.md"][kind] & K["b.md"][kind])
        if n < least:
            bad.append(f"{kind}: {n} совпадений, ждала не меньше {least}")
    if bad:
        print("канарейка метрики: НЕ ПОЙМАЛА — " + "; ".join(bad))
        return 1
    print("канарейка метрики: поймала — проза, таблица и фенс считаются раздельно")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Метрика пересказа между служебными документами")
    ap.add_argument("--wiki", default=".", help="корень проекта")
    ap.add_argument("--all-pairs", action="store_true", help="показать все пары, а не только очередь по прозе")
    ap.add_argument("--self-test", action="store_true", help="канарейка самой метрики")
    a = ap.parse_args()
    if a.self_test:
        return self_test()
    K, rows = measure(os.path.abspath(a.wiki), a.all_pairs)
    return report(os.path.abspath(a.wiki), rows, K)


if __name__ == "__main__":
    sys.exit(main())
