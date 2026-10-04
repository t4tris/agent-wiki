#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Кандидаты в страницы: их считают, а не вспоминают.

Зачем
-----
Порог создания страницы записан в `SCHEMA.md` («2+ источниках ИЛИ центральна для одного»),
но исполнителя у него не было: волна отдавала карточки и останавливалась, а вопрос «что из
этого тянет на страницу» решался по памяти. Здесь появляется счёт: термины берутся из
структурных полей карточек (`lineage_tools`, раздел «Концепты», раздел «Сущности») и из
страниц, варианты написания одного имени схлопываются (Cursor/cursor, Codex/codex,
`MCP (Model Context Protocol)`/`mcp`), и по каждому термину считаются источники и страницы.

Что считается решением
----------------------
* **страница** — страница заведена (или заводится в этой же волне);
* **отказ** — строка в очереди `_staging/candidate-decisions.tsv` с одной из причин:
  «мимолётное упоминание», «материала мало», «центральность не подтверждена».
Отказ закрывает кандидата: он больше не всплывает ни в карте, ни в проверке линтера (§44).

Порог — вопрос, а не приказ: машина считает и не даёт забыть, различает человек. Термин,
прошедший порог, — это вопрос «нужна ли страница?», а не решение «создать страницу».

Запуск
------
    python3 _toolkit/candidates.py list                  # висящие кандидаты (кому нет решения)
    python3 _toolkit/candidates.py list --all            # все, включая закрытые
    python3 _toolkit/candidates.py show <термин>         # где назван: источники и страницы
    python3 _toolkit/candidates.py decide <термин> page <слаг>
    python3 _toolkit/candidates.py decide <термин> refusal <причина>
    python3 _toolkit/candidates.py collect <отчёт.json>  # решения из отчёта волны (kind page-candidates)
    python3 _toolkit/candidates.py check                 # висящие кандидаты; код 1, если есть

Порог и разметка имён живут здесь в одном месте: карта (`topic_map.py`), линтер (§44),
контракт отчётов (`contract-v1.md`, kind `page-candidates`) и шаг волны «материал → страницы»
берут кандидатов отсюда, а не считают по-своему.
"""
import argparse
import glob
import json
import os
import re
import sys
from collections import defaultdict

import toolkit

# Порог схемы: термин назван не менее чем в стольких источниках (карточках).
# Про «ИЛИ центральна для одного» машина молчит: это слово человека (см. SCHEMA.md).
THRESHOLD = 2

# Вторая ветка порога: имя, которое называют многие страницы (наследник прежнего критерия
# «файл-конвенция, названный 5+ страницами»). Один порог на источники, второй — на страницы.
PAGES_THRESHOLD = 5

REASONS = ("мимолётное упоминание", "материала мало", "центральность не подтверждена",
           "полный синоним существующей страницы",
           # Удаление страницы — штатная операция (SCHEMA.md «Удаление и архивация»), и после него
           # термин снова набирает порог по карточкам. Без этой причины очередь воскрешала бы
           # каждую удалённую страницу вопросом «нужна ли страница?» — владелец 2026-09-18.
           "страница удалена решением владельца")

# Обобщающие слова в имени страницы: «bmad» + «method», «harness» + «architecture». Если термин покрывается
# именем существующей страницы ровно с таким уточнением, отдельная страница ему не нужна — владелец 2026-09-15:
# «у многих страниц неверная причина отказа. настоящая причина — уже существует полный синоним».
GENERIC = ("method", "methodology", "architecture", "framework", "agent", "cli", "code", "sdk",
           "api", "pattern", "approach", "technique", "tools", "guide", "spec", "workflow")


def _words(s):
    return re.sub(r"[^0-9a-zа-яё]+", " ", s.lower()).split()


def synonym_page(key, known):
    """Слаг существующей страницы, которая полностью покрывает термин (синоним), или пусто.

    Совпадение считается по нормализованным словам: либо имя страницы — это термин плюс обобщающее слово
    («bmad» → «bmad-method», «harness» → «harness-architecture»), либо наоборот.
    """
    kw = _words(key)
    if not kw:
        return ""
    for slug in known:
        pw = _words(slug.split("/")[-1])
        if pw == kw:
            continue                      # такая страница есть — кандидатом термин быть не должен
        if pw[:len(kw)] == kw and len(pw) > len(kw) and " ".join(pw[len(kw):]) in GENERIC:
            return slug
        if kw[:len(pw)] == pw and len(kw) > len(pw) and " ".join(kw[len(pw):]) in GENERIC:
            return slug
    return ""

# Одно имя под разными написаниями. Список короткий и явный: сюда попадает то,
# что машина схлопнуть не может, — перевод («клод» = «Claude») и сокращения.
ALIASES = {
    "agents sdk": "claude agent sdk",
    "клод": "claude",
    "клод код": "claude code",
    "мсп": "mcp",
    # Владелец 2026-09-18: «reward hacking до сих пор нет в кандидатах?» — термин разъезжался
    # на три написания: карточка манифеста знала его как `reward-hacking-penalties`,
    # проза страниц — как «взлом наград» и «Reward Hacking».
    # Ключи — в нормализованном виде (norm уже развернул дефисы в пробелы): карточка манифеста
    # зовёт понятие `reward-hacking-penalties`, проза страниц — «взлом наград» и «Reward Hacking».
    "reward hacking penalties": "взлом наград",
    "reward hacking": "взлом наград",
}

DECISIONS = "candidate-decisions.tsv"
# Реестр кандидатов, найденных прозой страниц (проверка 3 прохода lint). Отдельно от решений:
# решения принимает владелец, а «кандидат назван прозой» — факт о тексте, и его фиксирует проход.
PROSE = "prose-candidates.tsv"
PROSE_HEADER = "ключ\tимя\tгде найден\tдата\n"
HEADER = "ключ\tимя\tрешение\tпричина\tдата\n"

# Имена одного артефакта: конвенцию описывает указанная страница (решение владельца 2026-09-15).
SAME_CONVENTION = {
    # Вендорские имена одного файла инструкций. `SKILL.md` здесь НЕТ: решение владельца 2026-09-15 —
    # «SKILL.md — краеугольный термин, а не синоним skills-first-architecture», он считается как термин
    # и получает свою страницу (носитель навыка ≠ механизм навыков).
    "claude.md": "agents-md",
    "agents.md": "agents-md",
    "claude md": "agents-md",
    "agents md": "agents-md",
}

# Имена, которыми названа сама страница: показывать их «синонимами» нечего (подавление кандидата остаётся).
SELF_NAMED = {"agents.md"}

ARTIFACT = re.compile(r"\b[A-Z][A-Za-z0-9_.-]{1,30}\.(?:md|json|py|yml|yaml|toml|sh)\b")


def read(path):
    with open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def norm(name):
    """Ключ имени: регистр, дефисы, подчёркивания и скобочные уточнения не различаются."""
    s = (name or "").strip().strip("*`\"'«»").strip()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[_\-–—/]+", " ", s)
    s = re.sub(r"\s+", " ", s).strip().lower()
    s = re.sub(r"[.,;:]+$", "", s).strip()
    return ALIASES.get(s, s)


def cards_dir(root):
    return toolkit.area(root, "cards")


def page_dirs(vault):
    return [os.path.join(vault, d) for d in ("entities", "concepts", "comparisons", "queries")]


def pages_of(vault):
    out = {}
    for d in page_dirs(vault):
        for p in glob.glob(os.path.join(d, "*.md")):
            out[os.path.basename(p)[:-3]] = p
    return out


def card_terms(vault, root=None):
    root = root or os.path.dirname(vault)
    """Термины из структурных полей карточек: сколько карточек называют термин и каким написанием."""
    per_term = defaultdict(set)      # ключ -> {имена карточек}
    spelling = defaultdict(lambda: defaultdict(int))   # ключ -> {написание: раз}
    for p in sorted(glob.glob(os.path.join(cards_dir(root), "*.md"))):
        stem = os.path.basename(p)[:-3]
        t = read(p)
        names = []
        m = re.search(r"(?m)^lineage_tools:\s*\[(.*?)\]\s*$", t)
        if m:
            names += [x.strip().strip('"\'') for x in m.group(1).split(",") if x.strip()]
        for h in re.findall(r"(?m)^### (.+?)\s*$", t):
            # концепт — только заголовок с маркером «(тег-кандидат: …)»: это структурный признак,
            # остальные «### » в карточке — служебные подзаголовки ребёнка, а не понятия
            if "(тег-кандидат" not in h:
                continue
            name = re.sub(r"\s*\(тег-кандидат:.*$", "", h).strip().strip("*`").strip()
            if name:
                names.append(name)
        sec = t.split("## Сущности")
        if len(sec) > 1:
            for line in sec[1].split("\n## ")[0].split("\n"):
                m2 = re.match(r"-\s*\*\*(.+?)\*\*", line.strip())
                if m2:
                    names.append(m2.group(1).strip())
        for name in names:
            key = norm(name)
            if not key or len(key) < 2:
                continue
            per_term[key].add(stem)
            spelling[key][name] += 1
    return per_term, spelling


def page_terms(vault):
    """Термины со страниц: имена файлов-конвенций и точные слаги, названные на других страницах."""
    per_term = defaultdict(set)
    spelling = defaultdict(lambda: defaultdict(int))
    pages = pages_of(vault)
    known = {norm(slug): slug for slug in pages}
    for slug, path in pages.items():
        t = read(path)
        for m in ARTIFACT.finditer(t):
            key = norm(m.group(0))
            per_term[key].add(slug)
            spelling[key][m.group(0)] += 1
    return per_term, spelling, known


def mention_counts(vault, keys):
    """Сколько страниц называют термин в прозе (по нормализованному имени, с гибкими разделителями)."""
    pages = pages_of(vault)
    counts = defaultdict(int)
    pats = {}
    for key in keys:
        variants = [key] + [a for a, b in ALIASES.items() if b == key]
        pats[key] = re.compile(
            r"\b(?:" + "|".join(r"[\s_\-]*".join(re.escape(w) for w in v.split()) for v in variants) + r")\b",
            re.IGNORECASE)
    for slug, path in pages.items():
        text = read(path)
        for key, pat in pats.items():
            if pat.search(text):
                counts[key] += 1
    return counts


def load_decisions(root):
    """Решения из очереди: ключ -> (решение, причина, дата)."""
    path = toolkit.area(root, DECISIONS)
    out = {}
    if not os.path.exists(path):
        return out
    for i, line in enumerate(read(path).split("\n")):
        if not line.strip() or i == 0:
            continue
        parts = line.split("\t")
        if len(parts) >= 4:
            out[parts[0]] = (parts[2], parts[3], parts[4] if len(parts) > 4 else "")
    return out


def save_decision(root, key, name, decision, reason, date):
    path = toolkit.area(root, DECISIONS)
    rows = []
    if os.path.exists(path):
        rows = [l for l in read(path).rstrip("\n").split("\n")[1:] if l.strip()]
    rows = [l for l in rows if l.split("\t")[0] != key]
    rows.append("\t".join([key, name, decision, reason, date]))
    rows.sort(key=lambda l: l.split("\t")[0])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(HEADER + "\n".join(rows) + ("\n" if rows else ""))


def source_names(root):
    """Имена файлов-источников: это источники, а не термины — страницы не требуют (наследник прежнего
    правила «токен из sources: не конвенция»)."""
    names = set()
    for p in glob.glob(toolkit.raw(root, "**", "*.md"), recursive=True):
        base = os.path.basename(p)
        names.add(norm(base))
        names.add(norm(base[:-3]))
    return names


def prose_candidates(root):
    """Кандидаты, найденные прозой страниц: ключ -> {name, found, date}."""
    path = toolkit.area(root, PROSE)
    out = {}
    if not os.path.exists(path):
        return out
    for i, line in enumerate(read(path).split("\n")):
        if not line.strip() or i == 0:
            continue
        p = line.split("\t")
        if len(p) >= 3:
            # Ключ нормализуется при чтении: в списке прозы термин записан так, как его встретили
            # («/workflows», «hmm-вектор», «j-space»), а решение владельца лежит под нормализованным
            # ключом. Без этого решение находилось, но кандидат продолжал висеть в §43.
            out[norm(p[0])] = {"name": p[1], "found": p[2], "date": p[3] if len(p) > 3 else ""}
    return out


def add_prose_candidate(root, key, name, found, date):
    """Дописать кандидата, найденного прозой. Повтор не плодится: ключ один."""
    key = norm(key)
    path = toolkit.area(root, PROSE)
    rows = []
    if os.path.exists(path):
        rows = [l for l in read(path).rstrip("\n").split("\n")[1:] if l.strip()]
    for l in rows:
        if norm(l.split("\t")[0]) == key:
            return False
    rows.append("\t".join([key, name, found, date]))
    rows.sort(key=lambda l: l.split("\t")[0])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(PROSE_HEADER + "".join(l + "\n" for l in rows))
    return True


def from_lint(vault, root, reports, date=""):
    """Собрать кандидатов, найденных прозой, из отчётов прохода lint.

    Читает находки вида «концепт без страницы» и берёт поле `term` (обязательное для этого вида
    в контракте отчёта). Термин, у которого уже есть решение владельца, страница или запись в
    реестре прозы, пропускается. Отсутствие поля `term` — не молчаливый пропуск, а строка отчёта.
    """
    date = date or __import__("datetime").date.today().isoformat()
    known = {norm(slug) for slug in pages_of(vault)}
    _, _, known_names = page_terms(vault)
    decided = load_decisions(root)
    prose = prose_candidates(root)
    added, skipped = [], []
    for rp in reports:
        if not os.path.exists(rp):
            skipped.append((rp, "отчёта нет"))
            continue
        try:
            data = json.load(open(rp, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as ex:
            skipped.append((rp, "отчёт не читается: %s" % str(ex)[:60]))
            continue
        items = (data.get("findings") or []) + (data.get("items") or [])
        for it in items:
            kind = str(it.get("kind") or "")
            if "концепт" not in kind and "concept" not in kind:
                continue
            term = (it.get("term") or "").strip()
            if not term:
                skipped.append(("%s:%s" % (it.get("file"), it.get("line")), "в находке нет поля term"))
                continue
            key = norm(term)
            where = "%s:%s" % (it.get("file"), it.get("line"))
            if key in decided:
                skipped.append((term, "решение владельца уже есть"))
            elif key in prose:
                skipped.append((term, "уже в реестре прозы"))
            elif key in known or key.replace(".", " ") in known_names:
                skipped.append((term, "страница с таким слагом есть"))
            elif add_prose_candidate(root, key, term, where, date):
                added.append((key, term, where))
    return added, skipped


def candidates(vault, root):
    """Список кандидатов: термин, источники, страницы упоминания, ближайшая страница, решение."""
    by_sources, spell_s = card_terms(vault, root)
    by_pages, spell_p, known = page_terms(vault)
    all_keys = set(by_sources) | set(by_pages)
    src_names = source_names(root)
    keys = [k for k in all_keys
            if k not in src_names
            and (len(by_sources.get(k, ())) >= THRESHOLD or len(by_pages.get(k, ())) >= PAGES_THRESHOLD)]
    # Кандидаты, найденные прозой страниц (проверка 3 прохода lint). Их приносит не счёт по карточкам,
    # а чтение глазами, поэтому порог схемы к ним не применяется: раз термин назвали, вопрос уже задан.
    prose = prose_candidates(root)
    for k in prose:
        if k not in keys and k not in src_names:
            keys.append(k)
    pages_mention = mention_counts(vault, keys)
    decisions = load_decisions(root)
    out = []
    for key in keys:
        if (key in known or key.replace(".", " ") in known
                or key in SAME_CONVENTION):
            continue          # страница с таким слагом есть, или имя — синоним описанной конвенции
        decision, reason, date = decisions.get(key, ("", "", ""))
        spellings = defaultdict(int)
        for src in (spell_s.get(key), spell_p.get(key)):
            if src:
                for k, v in src.items():
                    spellings[k] += v
        name = max(spellings, key=spellings.get) if spellings else key
        # Имя для человека: если термин пришёл прозой страниц, в очереди виднее её написание
        # («Взлом наград (Reward Hacking)»), а не внутренний слаг карточки.
        if key in prose and (prose[key].get("name") or "").strip():
            name = prose[key]["name"].strip()
        near = sorted((s for s in known if s.startswith(key) or key.startswith(s)), key=len)
        synonyms = sorted(s for s in known if synonym_page(key, [s]))
        out.append({
            "synonym": synonyms[0] if synonyms else "",
            "key": key,
            "from_prose": key in prose,
            "found": prose.get(key, {}).get("found", ""),
            "name": name,
            "sources": len(by_sources.get(key, ())),
            "cards": sorted(by_sources.get(key, ())),
            "pages_named": sorted(by_pages.get(key, ())),
            "pages_mention": pages_mention.get(key, 0),
            "near": known[near[0]] if near else "",
            "decision": decision,
            "reason": reason,
            "date": date,
            "spellings": sorted(spellings),
        })
    out.sort(key=lambda r: (-r["sources"], -r["pages_mention"], r["key"]))
    return out


def hanging(vault, root):
    return [c for c in candidates(vault, root) if not c["decision"]]


def collect(root, report_path, force=False):
    """Слить решения из отчёта волны (kind page-candidates) в очередь.

    Расхождение с уже записанным решением не затирается молча. У владельца две выгрузки расходятся
    («Playwright MCP»: страница в одной, отказ в другой), и выбор между ними — его, а не программы:
    повторный сбор старой выгрузки иначе тихо откатил бы более новое слово. Перезапись — по `--force`.
    """
    data = json.load(open(report_path, encoding="utf-8"))
    known = load_decisions(root)
    added, skipped, conflicts, notes = [], [], [], []
    for it in data.get("items") or []:
        term, decision = it.get("term", ""), it.get("decision", "")
        key = norm(term)
        prev = known.get(key) or ("", "", "")
        if prev[0]:
            kind = "страница" if decision == "page" else ("отказ" if decision == "refusal" else "")
            value = (it.get("slug") or "") if kind == "страница" else (it.get("reason") or "")
            was = prev[0] + (f" ({prev[1]})" if prev[1] else "")
            now = kind + (f" ({value})" if value else "")
            if kind and prev[0] != kind and not force:
                # Разное решение по существу (страница против отказа) — выбор за владельцем.
                conflicts.append((key, f"в очереди «{was}», в файле «{now}»"))
                continue
            if kind and prev[0] == kind and (prev[1] or "") != value and not force:
                # Решение то же, уточнена формулировка (слаг или причина) — это не спор, а уточнение.
                notes.append((key, f"формулировка: было «{prev[1]}», в файле «{value}»"))
                continue
        if decision == "page" and it.get("slug"):
            save_decision(root, key, term, "страница", it["slug"], it.get("date", ""))
            added.append((key, "страница"))
        elif decision == "refusal" and it.get("reason"):
            if it["reason"] not in REASONS:
                skipped.append((key, f"причина вне списка: {it['reason']!r}"))
                continue
            save_decision(root, key, term, "отказ", it["reason"], it.get("date", ""))
            added.append((key, "отказ"))
        else:
            skipped.append((key, "нет решения или причины"))
    return added, skipped, conflicts, notes


def main():
    ap = argparse.ArgumentParser(description="Кандидаты в страницы: счёт, решения, проверка")
    ap.add_argument("command", choices=["list", "show", "decide", "collect", "check", "from-lint"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--root", default=None, help="корень проекта (по умолчанию — от вики вверх)")
    ap.add_argument("--all", action="store_true", help="list: показать и закрытых")
    ap.add_argument("--json", action="store_true", help="машинный вывод")
    ap.add_argument("--date", default="", help="дата решения (ГГГГ-ММ-ДД)")
    ap.add_argument("--force", action="store_true",
                    help="collect: перезаписать решение, расходящееся с уже записанным")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    if a.command == "from-lint":
        # Сбор кандидатов, названных прозой, из отчётов прохода lint:
        #   python3 _toolkit/candidates.py from-lint _staging/audit/lint-eye-*.json
        reports = [os.path.join(root, x) if not os.path.isabs(x) else x for x in (a.args or [])]
        added, skipped = from_lint(vault, root, reports, a.date)
        for key, term, where in added:
            print(f"добавлен кандидат из прозы: «{term}» ({where})")
        for who, why in skipped:
            print(f"пропущен {who}: {why}")
        print(f"\nдобавлено: {len(added)}; пропущено: {len(skipped)}; "
              f"реестр — _staging/{PROSE}; решения — обычным decide или дашбордом")
        return 0
    if a.command == "list":
        rows = candidates(vault, root)
        if not a.all:
            rows = [r for r in rows if not r["decision"]]
        if a.json:
            print(json.dumps(rows, ensure_ascii=False, indent=1))
            return 0
        if not rows:
            print("Пусто: у каждого кандидата порога есть решение — страница или отказ.")
            return 0
        for r in rows:
            state = f"{r['decision']} ({r['reason']})" if r["decision"] else "ВИСИТ"
            variant = f" | написания: {', '.join(r['spellings'])}" if len(r["spellings"]) > 1 else ""
            print(f"{r['sources']:>2} ист. | {r['pages_mention']:>2} стр. | {r['name'][:40]:<40} | {state}"
                  + (f" | похоже на {r['near']}" if r["near"] else "") + variant)
        print(f"\nвсего {len(rows)}; порог — {THRESHOLD}+ источника; причина отказа закрывает кандидата")
        return 0
    if a.command == "show":
        want = norm(" ".join(a.args))
        for r in candidates(vault, root):
            if r["key"] != want:
                continue
            print(json.dumps(r, ensure_ascii=False, indent=1))
            return 0
        print(f"кандидат {want!r} не найден (возможно, ниже порога или уже закрыт)")
        return 1
    if a.command == "decide":
        if len(a.args) < 2:
            print("нужно: decide <термин> page <слаг> | refusal <причина>")
            return 2
        term, kind = a.args[0], a.args[1]
        rest = " ".join(a.args[2:]).strip()
        if kind == "page":
            if not rest:
                print("для решения «страница» нужен слаг")
                return 2
            save_decision(root, norm(term), term, "страница", rest, a.date)
        elif kind == "refusal":
            if rest not in REASONS:
                print(f"причина должна быть одной из: {', '.join(REASONS)}")
                return 2
            save_decision(root, norm(term), term, "отказ", rest, a.date)
        else:
            print("решение — page или refusal")
            return 2
        print(f"записано: {term} -> {kind} ({rest})")
        return 0
    if a.command == "collect":
        if not a.args:
            print("нужен путь к отчёту")
            return 2
        added, skipped, conflicts, notes = collect(root, a.args[0], force=a.force)
        for k, v in added:
            print(f"  принято: {k} -> {v}")
        for k, why in skipped:
            print(f"  пропущено: {k} — {why}")
        for k, why in notes:
            print(f"  уточнение: {k} — {why}; оставлено прежнее")
        for k, why in conflicts:
            print(f"  КОНФЛИКТ: {k} — {why}; оставлено прежнее решение, перезапись — с --force")
        print(f"всего принято {len(added)}, пропущено {len(skipped)}, расхождений {len(conflicts)}")
        return 1 if (skipped or conflicts) else 0
    # check
    rows = hanging(vault, root)
    for r in rows:
        print(f"висящий кандидат: {r['name']} (источников {r['sources']}) — нет ни страницы, ни отказа")
    print(f"висящих кандидатов: {len(rows)}")
    return 1 if rows else 0


if __name__ == "__main__":
    sys.exit(main())
