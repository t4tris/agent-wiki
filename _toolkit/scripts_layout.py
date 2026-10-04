#!/usr/bin/env python3
"""Опись скриптов механизма: знает ли каждый свой каталог, читает ли домен, что делает без него.

Зачем. Развязка механизма и экземпляра — это три вопроса к каждому скрипту, и без описи ответы остаются
словами: «тринадцать мест ищут свой файл через корень» — утверждение о том, чего никто не пересчитал. Опись
считает машиной и кладёт в `_staging/scripts-layout.md`: по строке на скрипт и три числа сверху.

Что считается:
  * «свой каталог» — скрипт берёт путь к себе от `__file__` (`dirname(abspath(__file__))`), а не от корня проекта;
  * «читает домен» — скрипт импортирует `domain` и спрашивает у экземпляра его корпуса, реестры, слова темы;
  * «что без домена» — есть ли в скрипте явная ветка «не применимо»: без объявления домена проверка обязана
    молчать вслух, а не требовать чужие файлы.

    python3 _toolkit/scripts_layout.py --wiki .            # показать числа
    python3 _toolkit/scripts_layout.py --wiki . --write    # и записать опись
"""
import argparse
import ast
import glob
import os
import re
import sys

SELF = re.compile(r"dirname\(\s*os\.path\.abspath\(__file__\)\s*\)|dirname\(__file__\)|"
                  r"toolkit\.(?:script|python|TOOLKIT)\b")
DOMAIN = re.compile(r"import domain\b|from domain import")
NOT_APPLICABLE = re.compile(r"не применим|не применима|не объявил|домен[ау]?\s+не\s+заполнен|not_applicable")
TOOLKIT_VIA_ROOT = re.compile(
    r"os\.path\.join\(\s*[^)]*root[^)]*[\"']_staging[\"']\s*,\s*[\"'][\w.-]+\.py[\"']|"
    r"toolkit\.area\(\s*[^,]+,\s*[\"'](?![^\"']*\.local\.)[\w.-]+\.py[\"']"
)
ROOT_SCRIPT_LITERAL = re.compile(
    r"os\.path\.join\(\s*[^)]*root[^)]*[\"']_staging[\"']\s*,\s*[\"'][\w.-]+\.py[\"']"
)
OUT_PARTS = ("scripts-layout.md",)
# Своя папка — через `toolkit`: опись лежит в механизме, а её вывод — в рабочей области экземпляра.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import toolkit


def area_script_calls(text):
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return 0
    return sum(
        1 for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "area"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "toolkit"
        and any(isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                and arg.value.endswith(".py") and ".local." not in arg.value
                for arg in node.args)
    )


def rows(root):
    out = []
    for path in sorted(glob.glob(os.path.join(toolkit.TOOLKIT, "**", "*.py"), recursive=True)):
        name = os.path.relpath(path, toolkit.TOOLKIT).replace(os.sep, "/")
        if ".local." in name or name == "scripts_layout.py":
            continue          # файлы экземпляра и сама опись: её собственный образец правила не в счёте
        text = open(path, encoding="utf-8", errors="replace").read()
        out.append({
            "name": name,
            "self": bool(SELF.search(text)),
            "domain": bool(DOMAIN.search(text)),
            "na": bool(NOT_APPLICABLE.search(text)),
            "via_root": len(ROOT_SCRIPT_LITERAL.findall(text)) + area_script_calls(text),
        })
    return out


def local_rows(root):
    """Инструменты экземпляра: лежат в его области (`_staging/local/`), в поставку механизма не едут.

    Считаются отдельно, а не внутрь общих чисел: их место — не «сколько скриптов знают свой каталог», а сам
    факт, что они отделены от механизма.
    """
    path = toolkit.local(root)
    if not os.path.isdir(path):
        return []
    return [os.path.basename(f) for f in sorted(glob.glob(os.path.join(path, "*.py")))]


def main():
    ap = argparse.ArgumentParser(description="Опись скриптов: каталог, домен, поведение без домена")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true", help="записать `_staging/scripts-layout.md`")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    data = rows(root)
    n_self = sum(1 for r in data if r["self"])
    n_dom = sum(1 for r in data if r["domain"])
    n_na = sum(1 for r in data if r["na"])
    n_via = sum(r["via_root"] for r in data)
    local = local_rows(root)
    print(f"скриптов: {len(data)} | знают свой каталог: {n_self} | читают домен: {n_dom} | "
          f"говорят «не применимо»: {n_na} | ищут свой .py через корень: {n_via} | "
          f"инструментов экземпляра: {len(local)} (в поставку не едут)")
    if not a.write:
        return 0
    lines = ["# Опись скриптов: каталог, домен, поведение без домена", "",
             f"Считано `_toolkit/scripts_layout.py` на {len(data)} скриптах (файлы экземпляра с `.local.` не в счёте):",
             "",
             f"* знают свой каталог (`__file__` или `toolkit.script`): **{n_self}**",
             f"* читают объявление домена: **{n_dom}**",
             f"* говорят «не применимо», когда домена нет: **{n_na}**",
             f"* ищут свой `.py` через корень проекта (дефект §90): **{n_via}**",
             "",
             "| скрипт | свой каталог | домен | «не применимо» | .py через корень |",
             "| --- | --- | --- | --- | --- |"]
    for r in data:
        lines.append(f"| `{r['name']}` | {'да' if r['self'] else '—'} | {'да' if r['domain'] else '—'} | "
                     f"{'да' if r['na'] else '—'} | {r['via_root'] or '—'} |")
    if local:
        lines += ["", "## Инструменты экземпляра (в поставку не едут)", "",
                   "Написаны под материал и слова этого экземпляра, лежат в объявленной локальной области.",

                  "", "| скрипт |", "| --- |"]
        lines += [f"| `{name}` |" for name in local]
    target = toolkit.area(root, *OUT_PARTS)
    with open(target, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")
    print(f"опись записана: {os.path.join('_staging', *OUT_PARTS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
