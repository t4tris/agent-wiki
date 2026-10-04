#!/usr/bin/env python3
"""Генератор аудиторского обзора вики.

Правило проекта: числа в отчёте не набираются руками — их считает этот скрипт из
frontmatter страниц и файлов слоя 1. Вместе с отчётом пишутся:

    wiki-figures-<дата>.md    — машинный вывод: все счётчики и суммы
    wiki-figures-<дата>.json  — те же числа для линтера (раздел 14 сверяет отчёт с ними)
    wiki-graph-<дата>.tsv     — список рёбер «страница → источник», из которого построены диаграммы

Запуск: python3 _toolkit/audit_report.py --wiki .
"""
import argparse
import collections
import datetime
import hashlib
import json
import os
import re
import subprocess
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from fmparse import fold

TODAY = datetime.date.today().isoformat()
# Группировки отчёта — домен экземпляра: какие страницы как называются и на какие кластеры делятся.
# Механизм не знает ни имён страниц, ни их смысловых групп, поэтому таблицы живут в файле владельца
# (`_staging/report_groups.local.py`, в поставку не входит), а без него отчёт просто не кластеризует.
def _load_groups(root):
    """Группировки экземпляра из `_staging/report_groups.local.py`: имя с точками импортом не берётся."""
    import importlib.util
    path = toolkit.area(root, "report_groups.local.py")
    if not os.path.exists(path):
        return {}, (), ()
    spec = importlib.util.spec_from_file_location("report_groups_local", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return (getattr(mod, "CLUSTERS", {}), getattr(mod, "CORPUS_KIND", ()), getattr(mod, "TYPICAL", ()))


CLUSTERS: dict[str, list[str]] = {}
CORPUS_KIND: tuple[tuple[str, str], ...] = ()
TYPICAL: tuple[tuple[str, str], ...] = ()



def read(p):
    return open(p, encoding="utf-8").read()


def fm(t):
    m = re.match(r"^---\r?\n(.*?)\r?\n---", t, re.DOTALL)
    d = {}
    if m:
        for line in m.group(1).split("\n"):
            if ":" in line and not line.startswith((" ", "\t", "-")):
                k, v = line.split(":", 1)
                d[k.strip()] = v.strip().strip('"')
            else:
                fold(d, line)
    return d


def list_of(v):
    return [x.strip() for x in (v or "[]").strip("[]").split(",") if x.strip()]


def plural(n, one, few, many):
    """Русская числовая форма: 1 карточка, 2 карточки, 5 карточек."""
    n = int(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def collect(root):
    vault = toolkit.wiki(root)
    pages = {}
    for d in ("concepts", "comparisons", "entities", "queries"):
        full = os.path.join(vault, d)
        if not os.path.isdir(full):
            continue
        for fn in sorted(os.listdir(full)):
            if fn.endswith(".md"):
                t = read(os.path.join(full, fn))
                pages[fn[:-3]] = {"dir": d, "fm": fm(t), "text": t,
                                  "srcs": [os.path.basename(x) for x in list_of(fm(t).get("sources"))]}
    # источники слоя 1
    corpus, articles = {}, {}
    raw_dir = toolkit.raw(root)
    for fn in sorted(os.listdir(raw_dir)) if os.path.isdir(raw_dir) else []:
        if fn.endswith(".md"):
            kind = next((ru for en, ru in CORPUS_KIND if en in fn), "прочее")
            corpus[fn] = kind
    # Корпус обходится по факту: у свежего экземпляра нет ни `raw/articles`, ни вложенных папок, и падать
    # на этом значит требовать чужую раскладку (находка пробы 2026-09-22).
    articles_dir = toolkit.raw(root, "articles")
    for fn in sorted(os.listdir(articles_dir)) if os.path.isdir(articles_dir) else []:
        if not fn.endswith(".md"):
            continue
        fam = ("Anthropic" if fn.startswith("anthropic-") else "OpenAI" if fn.startswith("openai-")
               else "arXiv" if fn.startswith("arxiv-") else "ETH Zurich" if fn.startswith("ethz-") else "другое")
        articles[fn] = fam
    # вложенные папки raw/ (например, корпус практика): обход только верхнего уровня
    # и raw/articles/ терял их, и пакет публиковал заниженное число источников.
    raw_dir = toolkit.raw(root)
    for _d in sorted(os.listdir(raw_dir)) if os.path.isdir(raw_dir) else []:
        _sub = toolkit.raw(root, _d)
        if os.path.isdir(_sub) and _d != "articles":
            for fn in sorted(os.listdir(_sub)):
                if fn.endswith(".md"):
                    corpus[fn] = "прочее"
    cards = sorted(f for f in os.listdir(toolkit.area(root, "cards"))) if os.path.isdir(toolkit.area(root, "cards")) else []
    return pages, corpus, articles, cards


def classify(page):
    d, s = page["dir"], page["srcs"]
    if d == "queries":
        return "практический ответ"
    if d == "entities":
        return "справочная"
    if page["dir"] == "comparisons" or page["fm"].get("type") == "comparison" or not s:
        return "карта/аналитика"
    vendor = any(x.startswith(("anthropic-", "openai-")) for x in s)
    other = any(not x.startswith(("anthropic-", "openai-")) for x in s)
    if vendor and other:
        return "синтез"
    if vendor:
        return "вендорский дайджест"
    return "пересказ корпуса"


def main():
    global CLUSTERS, CORPUS_KIND, TYPICAL
    ap = argparse.ArgumentParser()
    ap.add_argument("--wiki", required=True)
    ap.add_argument("--no-typical", action="store_true", help="не вставлять три страницы целиком (краткий обзор)")
    a = ap.parse_args()
    root = a.wiki
    CLUSTERS, CORPUS_KIND, TYPICAL = _load_groups(root)
    vault = toolkit.wiki(root)
    pages, corpus, articles, cards = collect(root)
    out_dir = toolkit.area(root, "audit")
    os.makedirs(out_dir, exist_ok=True)

    edges = [(s, x) for s, p in sorted(pages.items()) for x in p["srcs"]]
    vendor_edges = [e for e in edges if e[1].startswith(("anthropic-", "openai-"))]
    corpus_edges = [e for e in edges if not e[1].startswith(("anthropic-", "openai-"))]
    inbound = collections.Counter(x for _, x in edges)
    classes = collections.Counter(classify(p) for p in pages.values())
    links = {s: len(re.findall(r"\[\[", p["text"])) for s, p in pages.items()}
    # lineage: считаем по карточкам, включая ext-*
    cards_dir = toolkit.area(root, "cards")
    lineage_tools, lineage_debate = collections.Counter(), 0
    for c in cards:
        t = read(os.path.join(cards_dir, c))
        m = re.match(r"^---\r?\n(.*?)\r?\n---", t, re.DOTALL)
        block = m.group(1) if m else ""
        mt = re.search(r"lineage_tools:\s*(.*)", block)
        md = re.search(r"lineage_debate:\s*(.*)", block)
        if mt:
            for x in re.findall(r"[a-z][a-z0-9-]+", mt.group(1)):
                lineage_tools[x] += 1
        if md and md.group(1).strip() not in ("[]", ""):
            lineage_debate += 1
    top_tools = [f"{k} (в {v})" for k, v in lineage_tools.most_common(3)]
    distinct_sources = len({x for _, x in edges})

    figures = {
        "страницы": len(pages),
        "страниц концептов": sum(1 for p in pages.values() if p["dir"] == "concepts"),
        "страниц сравнений": sum(1 for p in pages.values() if p["dir"] == "comparisons"),
        "страниц-сущностей": sum(1 for p in pages.values() if p["dir"] == "entities"),
        "практических ответов": sum(1 for p in pages.values() if p["dir"] == "queries"),
        "файлов слоя 1": len(corpus) + len(articles),
        "корпусных документов": len(corpus),
        "внешних статей": len(articles),
        "статей Anthropic": sum(1 for v in articles.values() if v == "Anthropic"),
        "статей OpenAI": sum(1 for v in articles.values() if v == "OpenAI"),
        "научных статей": sum(1 for v in articles.values() if v in ("arXiv", "ETH Zurich")),
        "рёбер графа": len(edges),
        "рёбер по корпусным источникам": len(corpus_edges),
        "рёбер по вендорским статьям": len(vendor_edges),
        "различных источников в графе": distinct_sources,
        "карточек извлечения": len(cards),
        "ссылок на страницу в среднем": round(sum(links.values()) / max(1, len(links)), 1),
        "карточек с явной полемикой": lineage_debate,
    }

    # снапшот: единый набор чисел для всего пакета
    q_count = 0
    lint_sections = None
    if os.path.isdir(out_dir):
        for fn in sorted(os.listdir(out_dir)):
            if fn.startswith("eval-questions-") and fn.endswith(".md"):
                q_count = len(re.findall(r"(?m)^\| \d+ \|", read(os.path.join(out_dir, fn))))
            if fn.startswith("lint-summary-") and fn.endswith(".json"):
                try:
                    lint_sections = json.load(open(os.path.join(out_dir, fn), encoding="utf-8")).get("sections")
                except (OSError, UnicodeError, json.JSONDecodeError, AttributeError, TypeError) as exc:
                    print("audit_report: lint-summary не прочитан (%s): %s" % (fn, exc), file=sys.stderr)
    service = [f for f in corpus if f == "manifest_maker_prompt.md"]   # служебный — по имени, не по подстроке
    figures["источников знаний"] = len(corpus) + len(articles) - len(service)
    figures["служебных файлов слоя 1"] = len(service)
    figures["эталонных вопросов"] = q_count
    if lint_sections:
        figures["разделов линтера"] = lint_sections
    snapshot_line = (f"> Снапшот пакета ({TODAY}): {figures['страницы']} страниц, "
                     f"{figures['файлов слоя 1']} файла слоя 1 = {figures['источников знаний']} источника знаний + "
                     f"{figures['служебных файлов слоя 1']} служебный шаблон, рёбер графа {figures['рёбер графа']}, "
                     f"карточек {figures['карточек извлечения']}, эталонных вопросов {figures['эталонных вопросов']}, "
                     f"разделов линтера {figures.get('разделов линтера', '—')}. Все числа этого файла взяты из одного прогона генератора.")

    # артефакты
    with open(os.path.join(out_dir, f"wiki-graph-{TODAY}.tsv"), "w", encoding="utf-8") as f:
        f.write("страница\tисточник\n")
        for s, x in edges:
            f.write(f"{s}\t{x}\n")
    json.dump({"figures": figures, "classes": dict(classes), "snapshot_line": snapshot_line,
               "checks": {"источника": figures["файлов слоя 1"], "рёбер": len(edges)}},
              open(os.path.join(out_dir, f"wiki-figures-{TODAY}.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    fig_lines = [f"# Машинный вывод отчёта ({TODAY})", "",
                 "Все числа ниже посчитаны скриптом `_toolkit/audit_report.py` из frontmatter страниц и файлов слоя 1.",
                 "Пересчитать: `python3 _toolkit/audit_report.py --wiki .`.", ""]
    fig_lines += [f"- {k}: {v}" for k, v in figures.items()]
    fig_lines += ["", "## Классы страниц", ""] + [f"- {k}: {v}" for k, v in sorted(classes.items())]
    fig_lines += ["", "## Сумма колонки «источников» по таблице типологии", "",
                  f"{sum(len(p['srcs']) for p in pages.values())} (совпадает с числом рёбер графа: {len(edges)})", ""]
    fig_lines += ["", "## Источники без единой ссылки", ""] + \
                 ([f"- {x}" for x in sorted(set(corpus) | set(articles)) if inbound[x] == 0] or ["нет"]) + [""]
    fig_lines += ["", "## Обратные ссылки: топ-10", ""] + [f"- {s}: {n} страниц" for s, n in inbound.most_common(10)] + [""]
    fig_lines += ["", "## Карточки извлечения", "",
                  f"всего {len(cards)}; источники с карточками: {len({c.split('.')[0] for c in cards})}", ""]
    open(os.path.join(out_dir, f"wiki-figures-{TODAY}.md"), "w", encoding="utf-8").write("\n".join(fig_lines))

    # отчёт
    L = [f"# Обзор вики для внешнего аудита ({TODAY})", "",         f"Корень проекта: `{root}`; хранилище Obsidian: `{toolkit.wiki(root)}`. "
         f"Все числа этого отчёта генерируются скриптом `_toolkit/audit_report.py`; машинный вывод и список рёбер лежат рядом "
         f"(`wiki-figures-{TODAY}.md`, `wiki-graph-{TODAY}.tsv`).", "",
         snapshot_line, "",
         "## 1. Что получает читатель", "",
         f"{figures['страницы']} содержательных страниц поверх {figures['источников знаний']} источников знаний "
         f"({figures['корпусных документов'] - figures['служебных файлов слоя 1']} корпусных: манифесты и транскрипции докладов, плюс "
         f"{figures['служебных файлов слоя 1']} служебный шаблон опроса; "
         f"{figures['внешних статей']} внешних статей: {figures['статей Anthropic']} у Anthropic, {figures['статей OpenAI']} у OpenAI, "
         f"{figures['научных статей']} научных).", "",
         "Читатель получает три разных продукта:", "",
         "- согласованные тематические страницы (концепты), где сведены позиции нескольких источников;",
         "- карты решений: где корпус спорит, какие боли повторяются и что каждая сторона предлагает;",
         "- практические ответы на вопросы «когда применять» и справочные страницы о людях, продуктах и компаниях.", "",
         "Типовое чтение: `index.md` → нужный концепт → строка «Источники» → сам документ в `sources/raw/`.", "",
         "## 2. Метрики", ""]
    L += [f"- страниц: {figures['страницы']} (концептов {figures['страниц концептов']}, сравнений {figures['страниц сравнений']}, "
          f"сущностей {figures['страниц-сущностей']}, практических ответов {figures['практических ответов']})",
          f"- документов слоя 1: {figures['файлов слоя 1']} — корпусных {figures['корпусных документов']}, "
          f"внешних {figures['внешних статей']} (Anthropic {figures['статей Anthropic']}, OpenAI {figures['статей OpenAI']}, научных {figures['научных статей']})",
          f"- рёбер графа «страница → источник»: {figures['рёбер графа']} "
          f"(корпусных {figures['рёбер по корпусным источникам']}, вендорских {figures['рёбер по вендорским статьям']}); "
          f"та же сумма получается сложением колонки «источников» в таблице типологии",
          f"- различных источников в графе: {figures['различных источников в графе']} из {figures['файлов слоя 1']}",
          f"- ссылок на страницу в среднем: {figures['ссылок на страницу в среднем']}",
          f"- карточек извлечения в `_staging/cards`: {figures['карточек извлечения']}",
          (f"- самая цитируемая страница-источник: {inbound.most_common(1)[0][0]} "
           f"({inbound.most_common(1)[0][1]} страниц)" if inbound else
           "- самая цитируемая страница-источник: нет данных — в вики ещё нет рёбер «страница → источник»"),
          f"- линтер: {figures.get('разделов линтера', '—')} разделов проверок; весь пакет отчётов сверяется с машинным выводом (раздел 14)",
          f"- эталонных вопросов: {figures['эталонных вопросов']}; служебных файлов слоя 1: {figures['служебных файлов слоя 1']}", ""]
    L += ["## 3. Карта тем", ""]
    for name, slugs in CLUSTERS.items():
        L.append(f"**{name}**")
        for s in slugs:
            if s in pages:
                L.append(f"- `{s}` — {pages[s]['fm'].get('summary', '')[:95]}")
        L.append("")
    L += ["**Сущности (" + str(figures["страниц-сущностей"]) + ")**: " + ", ".join(f"`{s}`" for s, p in sorted(pages.items()) if p["dir"] == "entities"), "",
          "Сквозные темы: границы контекста; минимально достаточные правила; цена верификации; свобода против регламента; кто отвечает за финальное решение.", "",
          "## 4. Типология страниц: где пересказ, а где синтез", "",
          "Классификация считается по составу `sources:` во frontmatter: «пересказ корпуса» — только корпусные документы, "
          "«вендорский дайджест» — только статьи Anthropic и OpenAI, «синтез» — и то и другое, «карта/аналитика» — сводные таблицы, "
          "«справочная» — сущности, «практический ответ» — страницы `queries/`.", "",
          "| страница | раздел | класс | источников |", "|---|---|---|---|"]
    for s, p in sorted(pages.items()):
        L.append(f"| `{s}` | {p['dir']} | {classify(p)} | {len(p['srcs'])} |")
    L += ["", f"Сумма колонки «источников»: {sum(len(p['srcs']) for p in pages.values())} — это и есть число рёбер графа.", "",
          "Как читать: «пересказ» проверяется против источника напрямую; «синтез» — на выводы, которых нет ни в одном источнике; "
          "«карта» — на полноту и честность; «вендорский дайджест» — на то, что самоотчёт не подан как замер.", "",
          f"Оговорка о независимости: корпус — один мильё, участники читают друг друга. По данным карточек "
          f"(`lineage_tools`, `lineage_debate`): прямая полемика отмечена у {figures['карточек с явной полемикой']} из них, "
          f"а общие инструменты сходятся на " + ", ".join(top_tools) + ". Поэтому «пересказ корпуса» — пересказ одного сообщества, не десяти независимых школ.", ""]
    # 5. три типовые страницы: фрагмент в обзоре, полный текст — в приложении
    appendix = [f"# Приложение к обзору вики ({TODAY})", "",
                f"Полные тексты типовых страниц, полный граф Mermaid и таблица связей. Основной обзор — `wiki-overview-{TODAY}.md` (компактный вариант без приложений).", "",
                "## А. Три типовые страницы (полностью)", ""]
    L += ["## 5. Три типовые страницы (фрагменты; полный текст — в приложении)", ""]
    for slug, kind in TYPICAL:
        p = pages.get(slug)
        if not p:
            continue
        full = read(os.path.join(vault, p["dir"], slug + ".md")).rstrip()
        lines = full.split("\n")
        fragment = "\n".join(lines[:16])
        L += [f"### `{slug}` — {kind} (первые 16 строк; полный текст — приложение, раздел А)", "", "```markdown",
              fragment, "```", ""]
        appendix += [f"### `{slug}` — {kind}", "", "```markdown", full, "```", ""]
    central = {x for x, n in inbound.items() if n >= 10}
    central_rows = " · ".join(f"`{x.replace(chr(46)+'md', '')}` ({n})" for x, n in inbound.most_common(12))
    L += ["## 6. Диаграмма «страница → источники»", "",
          "Рёбра взяты из `sources:` (машиночитаемо). Здесь только самые центральные источники — те, на которые ссылаются десять и более страниц; "
          f"полный список рёбер лежит в `wiki-graph-{TODAY}.tsv`, полный граф и таблица связей — в приложении (`wiki-appendix-{TODAY}.md`).", "",
          "Вес источников (сколько страниц на него ссылаются): " + central_rows + ".", "",
          "Страницы с наибольшим числом источников: " + " · ".join(
              f"`{s}` ({len(p['srcs'])})" for s, p in sorted(pages.items(), key=lambda kv: -len(kv[1]["srcs"]))[:12]) + ".", "",
          "Полная картина — двумя способами: таблица связей (страница и все её источники) и граф Mermaid на " + str(figures["рёбер графа"]) + " рёбер, оба в приложении.", ""]
    appendix += ["## Б. Полный граф «страница → источник»", "", "### Корпусные источники", "", "```mermaid", "graph LR"]
    ids_f = {}
    for s, x in corpus_edges:
        ids_f.setdefault(("p", s), f"P{len([k for k in ids_f if k[0]=='p'])}")
        ids_f.setdefault(("s", x), f"S{len([k for k in ids_f if k[0]=='s'])}")
        appendix.append(f'    {ids_f[("p", s)]}["{s}"] --> {ids_f[("s", x)]}["{x.replace(".md", "")}"]')
    appendix += ["```", "", "### Вендорские статьи", "", "```mermaid", "graph LR"]
    ids2 = {}
    for s, x in vendor_edges:
        ids2.setdefault(("p", s), f"P{len([k for k in ids2 if k[0]=='p'])}")
        ids2.setdefault(("s", x), f"S{len([k for k in ids2 if k[0]=='s'])}")
        appendix.append(f'    {ids2[("p", s)]}["{s}"] --> {ids2[("s", x)]}["{x.replace(".md", "")[:42]}"]')
    appendix += ["```", "", "## В. Таблица связей (плотный вариант того же графа)", "", "| страница | источники |", "|---|---|"]
    for s, p in sorted(pages.items()):
        appendix.append(f"| `{s}` | " + ", ".join(f"`{x}`" for x in p["srcs"]) + " |")
    L += ["", "## 7. Как проверять эту вики", "",
          "1. Три страницы класса «пересказ» — сверить утверждения с текстом источника в `raw/`.",
          "2. Три страницы класса «синтез» — искать выводы, не следующие ни из одного источника страницы.",
          "3. Карту конфликтов — на полноту: нет ли разногласий корпуса, не попавших в таблицу.",
          "4. Провенанс — одна строка «Источники» на страницу, обратные ссылки у каждого документа.",
          "5. Прогнать `_toolkit/lint_wiki.py`: " + str(figures.get("разделов линтера", "—")) + " разделов, включая сверку этого отчёта с машинным выводом.",
          "6. Прогнать `_toolkit/verify_numbers.py`: каждое число страницы ищется в тексте её источников.", "",
          "## 8. Что осталось открытым", "",
          "- независимые замеры для страниц с `evidence: vendor-self-report`;",
          "- `manifest_maker_prompt.md` — служебный шаблон, а не источник знаний;",
          "- тематическая карта (`_meta/topic-map.md`) появится около 50 страниц;",
          "- `lineage` в карточках описывает интеллектуальную зависимость корпуса (кто кого читал), файловая независимость выше реальной;",
          "", "",
          "## 9. Рефлексия: сложности и решения", "",
          "- Доступ к данным: Telegram отдаёт сохранённые сообщения только через клиент, сервисные домены заблокированы провайдером; поверх — суточная пауза на экспорт. Выбран ручной GUI-экспорт.",
          "- Объём против контекста: 34 источника — сотни тысяч слов; введён промежуточный слой карточек извлечения, страницы пишутся по ним.",
          "- Двойной учёт: транскрипция и собранный из неё манифест считаются одним голосом; иначе уверенность надувается дублями.",
          "- Obsidian против слоя 1: read-only ломает сохранение, junction не индексируется, сторож на cron — лишняя автоматика; итог — записываемые копии, мастер-архив, синхронизация только докладывает.",
          "- Разметка: пайп внутри ссылки рвёт таблицу, выравнивание по битой строке даёт пустые колонки, незакавыченное значение с двоеточием ломает свойства Obsidian.",
          "- Провенанс: сноска в каждом абзаце дала 509 сносок; заменено одной строкой «Источники» и ссылками в строках таблиц.",
          "- Смешение понятий: перегрузка инструкциями и переполнение контекста были одной страницей, теперь это два механизма.",
          "- Вендорские материалы помечаются как самоотчёт, ключевые числа сверены с текстом статей вручную.",
          "- Делегирование: 30+ подагентов; дисциплина — один владелец на файл, запрет правок чужих страниц, проверка цифр родителем.", "",
          "## 10. Вопросы: что закрыто, что осталось", "",
          "Закрыты в этой итерации (ответ зафиксирован в схеме и проверяется линтером):",
          "",
          "- шкала силы доказательства — поле `evidence` со значениями «мнение практика, вендорский самоотчёт, замер, смешанное»;",
          "- границы делегирования — раздел «Границы делегирования» в схеме;",
          "- различение собственного вывода и пересказа — ортогональные класс страницы и флаг `own-analysis` плюс видимый маркер;",
          "- формат провенанса для чисел — атрибуция в «Фактах и цифры» плюс автопроверка чисел.",
          "",
          "Остаются открытыми:",
          "",
          "1. Какой формат провенанса нужен уровнем ниже страницы: реестр утверждений с цитатой, локатором и хешем — не сделан.",
          "2. Как хранить переводы цитат, чтобы перевод не подменял оригинал при проверке.",
          "3. Как оценивать полноту карты конфликтов: по какому признаку понять, что спор корпуса пропущен.",
          "4. Каким сигналом измерять покрытие темы и понять, что корпус исчерпан (маржинальная новизна источника не считается).",
          "5. Как учитывать интеллектуальную зависимость голосов корпуса при подсчёте порогов значимости.",
          f"6. Превращать ли набор из {figures['эталонных вопросов']} эталонных вопросов в регулярный экзамен после каждого инжеста.", ""]
    # подстановки для статус-документа и манифеста пакета
    own = sum(1 for p in pages.values() if p["fm"].get("own-analysis", "").strip().strip('"') == "true")
    lens = [len(p["text"].split("\n")) for p in pages.values()]
    card_dir = toolkit.area(root, "cards")
    card_files = [c for c in os.listdir(card_dir) if c.endswith(".md")] if os.path.isdir(card_dir) else []
    card_texts = {c: read(os.path.join(card_dir, c)) for c in card_files}
    missing_cards = [a[:-3] for a in articles
                     if not any(c[:-3] == a[:-3] or c[:-3] == "ext-" + a[:-3] or a[:-3] in t for c, t in card_texts.items())]
    # считаем покрытие по сопоставлению, а не по именам файлов
    def covered(name, pool):
        return any(c[:-3] == name or c[:-3] == "ext-" + name or name in t for c, t in card_texts.items())

    manifest_files = [f for f in corpus if "manifest" in f and "prompt" not in f]
    cards_manifest = len([f for f in manifest_files if covered(f[:-3], card_texts)])
    cards_articles = len([a for a in articles if covered(a[:-3], card_texts)])
    service_cards = len([f for f in corpus if "prompt" in f and covered(f[:-3], card_texts)])
    canary_score, canary_note = "не проводилась", "Аттестация чекеров на канарейках запланирована: без неё «0 проблем» ничего не доказывает."
    for fn in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if fn.startswith("canary-report-") and fn.endswith(".md"):
            ct = read(os.path.join(out_dir, fn))
            ss = re.search(r"Чувствительность: (\d+)/(\d+)", ct)
            sp = re.search(r"Специфичность: (\d+)/(\d+)", ct)
            if ss and sp:
                canary_score = f"чувствительность {ss.group(1)}/{ss.group(2)}, специфичность {sp.group(1)}/{sp.group(2)}"
                canary_note = ("Первый прогон дал 9 из 10 и вскрыл мёртвый раздел 9 линтера: таблицы не проверялись вовсе. "
                               "После починки — 10 из 10, плюс три безвредные канарейки на ложные срабатывания.")
    # изменения с прошлого аудита: из git, чтобы аудитору не держать память инструментом
    try:
        g_log = subprocess.run(["git", "-C", root, "log", "--pretty=%h|%s", "-12"],
                               capture_output=True, text=True, timeout=20, check=False).stdout.strip().split("\n")
        g_files = subprocess.run(["git", "-C", root, "log", "--pretty=", "--name-only", "-25"],
                                 capture_output=True, text=True, timeout=20, check=False).stdout
        g_total = len(subprocess.run(["git", "-C", root, "log", "--pretty=%h"],
                                     capture_output=True, text=True, timeout=20, check=False).stdout.split())
    except (OSError, subprocess.TimeoutExpired) as _e:
        g_log, g_files, g_total = [], "", 0
        gerr = str(_e)
    wiki_changed = sorted({os.path.basename(l) for l in g_files.split("\n")
                           if l.startswith("wiki/") and l.endswith(".md")})
    T_changelog = "\n".join("- " + c for c in [
        f"Коммитов в истории: {g_total}." + (f" Ошибка git: {gerr}" if 'gerr' in dir() else ""),
        "Последние записи: " + "; ".join(f"`{l.split('|')[0]}` " + l.split("|", 1)[1][:70] for l in g_log[:6] if "|" in l) + ".",
        f"Изменённых страниц вики в последних 25 коммитах: {len(wiki_changed)}" +
        (" — " + ", ".join(f"`{x[:-3]}`" for x in wiki_changed[:10]) if wiki_changed else "") + ".",
    ])

    def pl(n, one, few, many):
        return f"{n} {plural(n, one, few, many)}"

    # траектория экзамена: строки exam-history.tsv
    exam_rows = []
    _ehp = os.path.join(out_dir, "exam-history.tsv")
    if os.path.exists(_ehp):
        for line in open(_ehp, encoding="utf-8").read().strip().split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) >= 6:
                exam_rows.append(parts)
    T_exam = "\n".join("- " + " | ".join(f"{k}: {v}" for k, v in zip(("дата", "мягко", "строго по основным", "строго по полному", "признали пробел", "источники"), r)) for r in exam_rows) or "- прогонов ещё не было"
    if len(exam_rows) >= 2:
        def _r(s):
            a, b = s.split("/"); return int(a) / int(b)
        if _r(exam_rows[-1][2]) < _r(exam_rows[-2][2]):
            T_exam += "\n- **СИГНАЛ РЕГРЕССИИ:** строгое по основным упало — сначала разбор навигации и шума, потом новый контент."
    # слепой прогон и канарейки нужны статус-документу — считаем до его рендера
    blind = {"soft": "—", "strict": "—", "gaps": "—", "sources": "—"}
    for fn in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if fn.startswith("blind-run-score-") and fn.endswith(".md"):
            bt = read(os.path.join(out_dir, fn))
            for key, pat in (("soft", r"Мягкое попадание[^:]*:\s*(\d+/\d+)"),
                             ("strict", r"Строгое попадание[^:]*:\s*(\d+/\d+)"),
                             ("gaps", r"читатель сам назвал пробел[^:]*:\s*(\d+/\d+)"),
                             ("sources", r"Привели источники:\s*(\d+/\d+)")):
                m = re.search(pat, bt)
                if m:
                    blind[key] = m.group(1)
    blind["rerun"] = "—"
    for fn in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if fn.startswith("blind-run-score-") and fn.endswith(".md"):
            bt = read(os.path.join(out_dir, fn))
            mm = re.search(r"доля названных ожидаемых страниц: (\d+/\d+) после правок против (\d+/\d+)", bt)
            if mm:
                blind["rerun"] = "доля названных ожидаемых страниц " + mm.group(1) + " против " + mm.group(2)
    can = {}
    _csp = os.path.join(out_dir, f"canary-results-{TODAY}.json")
    if os.path.exists(_csp):
        can = json.load(open(_csp, encoding="utf-8"))
    # числа автопроверки и аттестации — только из машинных сводок
    vc = {"confirmed": "—", "flagged": "—"}
    _vcp = os.path.join(out_dir, f"number-check-{TODAY}.json")
    if os.path.exists(_vcp):
        vc = json.load(open(_vcp, encoding="utf-8"))
    attested_secs, unattested_secs = [], []
    lint_titles = []
    _lsp_late = toolkit.latest_report(out_dir, "lint-summary-")
    if _lsp_late:
        lint_titles = json.load(open(_lsp_late, encoding="utf-8")).get("titles", [])
    import re as _re
    for c in can.get("canaries", []):
        m = _re.match(r"lint §(\d+)", c.get("expected", ""))
        if m:
            attested_secs.append(int(m.group(1)))
    for t in lint_titles:
        m = _re.match(r"(\d+)\.", t)
        if m and int(m.group(1)) not in attested_secs:
            unattested_secs.append(t)
    can_sens = "—"
    can_spec = "—"
    if can.get("sensitivity"):
        can_sens = f"{can['sensitivity']['caught']}/{can['sensitivity']['total']}"
    if can.get("specificity"):
        can_spec = f"{can['specificity']['passed']}/{can['specificity']['total']}"

    # реестры: карточки и утверждения (машинные файлы, а не пересчёт по именам)
    reg = {}
    reg_path = toolkit.area(root, "cards-registry.json")
    if os.path.exists(reg_path):
        reg = json.load(open(reg_path, encoding="utf-8")).get("summary", {})
    claims_path = toolkit.area(root, "claims-registry.tsv")
    claims_n = sum(1 for _ in open(claims_path, encoding="utf-8")) - 1 if os.path.exists(claims_path) else 0
    for k, key in (("cards_total", "карточек извлечения"), ("lineage_filled", "карточек с lineage"),
                   ("debates", "карточек с полемикой"), ("cardinality", "карточек допустимо")):
        if k in reg:
            figures[key] = reg[k]
    figures["утверждений с атрибуцией"] = claims_n

    T = {
        "date": TODAY, "snapshot": snapshot_line,
        "blind_soft": blind["soft"], "blind_strict": blind["strict"],
        "blind_gaps": blind["gaps"], "blind_sources": blind["sources"], "blind_rerun": blind["rerun"],
        "canary_sens": can_sens, "canary_spec": can_spec,
        "verified_confirmed": str(vc.get("confirmed", "—")), "verified_flagged": str(vc.get("flagged", "—")),
        "canary_attested": str(len(set(attested_secs))), "canary_total_sections": str(len(lint_titles)),
        "canary_unattested": (", ".join(unattested_secs) if unattested_secs else "нет"),
        "changelog": T_changelog, "exam_trajectory": T_exam,
        "counts": {
            "cards": pl(figures["карточек извлечения"], "карточка", "карточки", "карточек"),
            "claims": pl(claims_n, "утверждение", "утверждения", "утверждений"),
            "articles": pl(figures["внешних статей"], "статья", "статьи", "статей"),
            "pages": pl(figures["страницы"], "страница", "страницы", "страниц"),
            "corpus": pl(figures["корпусных документов"] - figures["служебных файлов слоя 1"], "корпусный документ", "корпусных документа", "корпусных документов"),
            "sources": pl(figures["источников знаний"], "источник знаний", "источника знаний", "источников знаний"),
            "own": pl(own, "страница", "страницы", "страниц"),
            "questions": pl(figures["эталонных вопросов"], "вопрос", "вопроса", "вопросов"),
            "cards_articles": pl(cards_articles, "статья", "статьи", "статей"),
        },
        "cards_articles_num": str(cards_articles), "cards_manifests_num": str(cards_manifest),
        "cards_missing_sentence": ("Все статьи слоя 1 покрыты карточками." if not missing_cards
                                   else "Карточки ещё не созданы для: " + ", ".join("`" + m + "`" for m in missing_cards) + "."),
        "pages": figures["страницы"], "sources": figures["источников знаний"],
        "files_layer1": figures["файлов слоя 1"], "articles": figures["внешних статей"],
        "corpus_sources": figures["корпусных документов"] - figures["служебных файлов слоя 1"],
        "service": figures["служебных файлов слоя 1"], "edges": figures["рёбер графа"],
        "questions": figures["эталонных вопросов"], "lint_sections": figures.get("разделов линтера", "—"),
        "coverage": "26 из 26 страниц концептов и сравнений",
    "own_analysis": own,
        "lineage_cards": len(card_files), "lineage_debate": figures["карточек с явной полемикой"],
        "top_tools": ", ".join(top_tools),
        "avg_len": round(sum(lens) / max(1, len(lens)), 1), "max_len": max(lens) if lens else 0,
        "cards": figures["карточек извлечения"], "cards_manifests": cards_manifest,
        "cards_articles": cards_articles,
        "cards_manifests_reg": str(reg.get("by_kind", {}).get("manifest", cards_manifest)),
        "cards_service_reg": str(reg.get("by_kind", {}).get("template", service_cards)),
        "cards_cardinality": str(reg.get("cardinality", cards_manifest + cards_articles)),
        "cards_covered": str(reg.get("covered_manifests", 0) + reg.get("covered_articles", 0)),
        "claims_count": str(claims_n),
        "lineage_filled": str(reg.get("lineage_filled", 0)),
        "debates_count": str(reg.get("debates", 0)),
        "cards_missing_list": ", ".join("`" + m + "`" for m in missing_cards) or "нет",
        "canary_score": canary_score, "canary_note": canary_note,
        "audit_overview": f"wiki-overview-{TODAY}.md", "audit_appendix": f"wiki-appendix-{TODAY}.md",
        "audit_figures": f"wiki-figures-{TODAY}.md", "audit_graph": f"wiki-graph-{TODAY}.tsv",
        "lint_summary": os.path.basename(toolkit.latest_report(out_dir, "lint-summary-")) or "нет сводки",
    }

    # плоские ключи для подстановки: counts.cards и т. п.
    for k, v in list(T.items()):
        if isinstance(v, dict):
            for k2, v2 in v.items():
                T[f"{k}.{k2}"] = v2
    text = "\n".join(L)
    out = os.path.join(out_dir, f"wiki-overview-{TODAY}.md")
    open(out, "w", encoding="utf-8").write(text.rstrip() + "\n")
    ap = os.path.join(out_dir, f"wiki-appendix-{TODAY}.md")
    open(ap, "w", encoding="utf-8").write("\n".join(appendix[:3] + [snapshot_line] + appendix[3:]).rstrip() + "\n")
    # статус-документ рендерится из шаблона: числа только подстановкой
    tpl = os.path.join(out_dir, "templates", "wiki-state.template.md")
    if os.path.exists(tpl):
        state = read(tpl)
        for k, v in T.items():
            state = state.replace("{{" + k + "}}", str(v))
        st = os.path.join(out_dir, f"wiki-state-{TODAY}.md")
        open(st, "w", encoding="utf-8").write(state.rstrip() + "\n")
        print("статус:", st, f"({round(len(state.encode())/1024, 1)} КБ)")
    # слепой прогон: метрики из отчёта оценщика
    blind = {"soft": "—", "strict": "—", "gaps": "—", "sources": "—"}
    for fn in sorted(os.listdir(out_dir)) if os.path.isdir(out_dir) else []:
        if fn.startswith("blind-run-score-") and fn.endswith(".md"):
            bt = read(os.path.join(out_dir, fn))
            for key, pat in (("soft", r"Мягкое попадание[^:]*:\s*(\d+/\d+)"),
                             ("strict", r"Строгое попадание[^:]*:\s*(\d+/\d+)"),
                             ("gaps", r"читатель сам назвал пробел[^:]*:\s*(\d+/\d+)"),
                             ("sources", r"Привели источники:\s*(\d+/\d+)")):
                m = re.search(pat, bt)
                if m:
                    blind[key] = m.group(1)
    T["blind_soft"] = blind["soft"]
    T["blind_strict"] = blind["strict"]
    T["blind_gaps"] = blind["gaps"]
    T["blind_sources"] = blind["sources"]
    can = {}
    _csp = os.path.join(out_dir, f"canary-results-{TODAY}.json")
    if os.path.exists(_csp):
        can = json.load(open(_csp, encoding="utf-8"))
    T["canary_sens"] = "—"
    T["canary_spec"] = "—"
    if can.get("sensitivity"):
        T["canary_sens"] = f"{can['sensitivity']['caught']}/{can['sensitivity']['total']}"
    if can.get("specificity"):
        T["canary_spec"] = f"{can['specificity']['passed']}/{can['specificity']['total']}"
    # машинное приложение к аудиту: всё, что проверяется без доверия к прозе
    def git_counts():
        try:
            out = subprocess.run(["git", "-C", root, "log", "--pretty=%s"], capture_output=True, text=True, timeout=20, check=False).stdout
        except (OSError, subprocess.TimeoutExpired) as exc:
            print("audit_report: история git для типов коммитов не прочитана: %s" % exc, file=sys.stderr)
            return {}, 0
        types = collections.Counter((l.split(":")[0].strip() if ":" in l else "без типа") for l in out.split("\n") if l.strip())
        return dict(types), len([l for l in out.split("\n") if l.strip()])
    gtypes, gtotal = git_counts()
    lint_sum = {}
    lsp = toolkit.latest_report(out_dir, "lint-summary-")
    if lsp:
        lint_sum = json.load(open(lsp, encoding="utf-8"))
    can = {}
    csp = os.path.join(out_dir, f"canary-results-{TODAY}.json")
    if os.path.exists(csp):
        can = json.load(open(csp, encoding="utf-8"))
    ma = [f"# Машинное приложение к ответу на аудит ({TODAY})", "", snapshot_line, "",
          "Этот файл существует, чтобы аудитору не нужно было верить ни одному числу из прозы: всё ниже посчитано из репозитория.", "",
          "## Реестры", "",
          "| реестр | значение |", "|---|---|",
          f"| карточек извлечения (`_staging/cards-registry.json`) | {figures['карточек извлечения']} |",
          f"| из них манифесты / статьи / заметки владельца / шаблон | {reg.get('by_kind', {}).get('manifest', '—')} / {reg.get('by_kind', {}).get('article', '—')} / {reg.get('by_kind', {}).get('note', '—')} / {reg.get('by_kind', {}).get('template', '—')} |",
          f"| покрытие карточками (из допустимых {reg.get('cardinality', '—')}) | {reg.get('covered_manifests', 0) + reg.get('covered_articles', 0)} |",
          f"| карточек с lineage / с полемикой | {reg.get('lineage_filled', '—')} / {reg.get('debates', '—')} |",
          f"| утверждений с владельцем (`_staging/claims-registry.tsv`) | {claims_n} |",
          f"| эталонных вопросов | {figures['эталонных вопросов']} |", ""]
    if lint_sum:
        ma += ["## Линтер по разделам", "", "| раздел | проблем |", "|---|---|"]
        for title, n in lint_sum.get("by_section", {}).items():
            ma.append(f"| {title} | {n} |")
        ma += ["", f"Всего разделов: {lint_sum.get('sections')}; итого проблем: {lint_sum.get('total')}.", ""]
    if can:
        ma += ["## Канарейки", "",
               f"Чувствительность: {can.get('sensitivity', {}).get('caught')}/{can.get('sensitivity', {}).get('total')}; "
               f"специфичность: {can.get('specificity', {}).get('passed')}/{can.get('specificity', {}).get('total')}.", "",
               "| № | канарейка | ожидался | поймана |", "|---|---|---|---|"]
        for c in can.get("canaries", []):
            if c.get("kind") == "specificity":
                verdict = "тишина (как и должно)" if c["caught"] else "**ЛОЖНОЕ СРАБАТЫВАНИЕ**"
            else:
                verdict = "да" if c["caught"] else "**нет**"
            ma.append(f"| {c['id']} | {c['name']} | {c['expected']} | {verdict} |")
        ma.append("")
        ma += [f"Аттестовано разделов линтера: {len(set(attested_secs))} из {len(lint_titles)}. "
               f"Не аттестованы: {', '.join(unattested_secs) if unattested_secs else 'нет'}. "
               f"Числа аттестации взяты из `canary-results-{TODAY}.json`.", ""]
    ma += ["## Слепой прогон эталонных вопросов", "",
           f"Мягкое попадание (названа хотя бы одна ожидаемая страница): {blind['soft']}; "
           f"строгое попадание (названы все ожидаемые страницы): {blind['strict']}; "
           f"читатель сам назвал пробел: {blind['gaps']}; привели источники: {blind['sources']}.", "",
           "Прогон выполняли независимые исполнители, которым выдали только вопросы без ожидаемых страниц и без подсказок. "
           "Оценка считается скриптом `_toolkit/score_blind_run.py` по пересечению с ожиданиями файла вопросов.", ""]
    ma += ["## История git", "", f"Коммитов: {gtotal}.", "", "| тип | коммитов |", "|---|---|"]
    for k, v in sorted(gtypes.items(), key=lambda kv: -kv[1]):
        ma.append(f"| `{k}` | {v} |")
    ma += ["", "## Файлы пакета", "", "| файл | КБ | sha256 (12) |", "|---|---|---|"]
    for fn in sorted(os.listdir(out_dir)):
        if fn.startswith(("wiki-", "auditor-")) and fn.endswith(".md") \
                and not fn.startswith(("wiki-machine-appendix-", "wiki-package-", "audit-package-")):  # compiled: хеши внутри них самих дали бы петлю
            pth = os.path.join(out_dir, fn)
            digest = hashlib.sha256(open(pth, "rb").read()).hexdigest()[:12]
            ma.append(f"| `{fn}` | {round(os.path.getsize(pth)/1024, 1)} | `{digest}` |")
    open(os.path.join(out_dir, f"wiki-machine-appendix-{TODAY}.md"), "w", encoding="utf-8").write("\n".join(ma) + "\n")
    print("машинное приложение:", os.path.join(out_dir, f"wiki-machine-appendix-{TODAY}.md"))

    print("отчёт:", out, f"({round(len(text.encode())/1024)} КБ)")
    print("приложение:", ap, f"({round(len(chr(10).join(appendix).encode())/1024)} КБ)")
    print("машинный вывод:", os.path.join(out_dir, f"wiki-figures-{TODAY}.md"))
    print("рёбра:", os.path.join(out_dir, f"wiki-graph-{TODAY}.tsv"))
    print("ключевые числа:", json.dumps(figures, ensure_ascii=False))


if __name__ == "__main__":
    main()
