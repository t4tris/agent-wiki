#!/usr/bin/env python3
"""Фьюзинг расшифровок: канон (whisper) + второй голос (GigaAM) → одна версия с журналом решений.

Идея: там, где модели согласны, текст очевиден. Там, где расходятся, выбор делается по правилам,
и КАЖДОЕ решение записывается в журнал — иначе получится ещё одна версия, доверия к которой нет.

Правила (v1):
  R1 латиница     — вариант с латинскими буквами важнее транслитерации: `opencode` против «опин код».
  R2 глоссарий    — термины из области (инструменты, агентные практики) важнее прочего.
  R3 филлеры      — «а-а», «э-э», «ну», «вот» отбрасываются, GigaAM пишет дословно и ими сорит.
  R4 склейка      — подозрительно длинный токен без гласных или >18 знаков хуже короткого: «известныевизвимости».
  R5 ничья        — при равенстве берётся канон (whisper), решение всё равно попадает в журнал.
  R6 флаг         — если оба варианта подозрительны, фрагмент помечается «слушать» и НЕ решается автоматически.

    python3 _toolkit/tools/tg-saved/fuse_transcripts.py --canon <папка> --second <папка> --out <папка фьюзинга>
"""
import argparse
import collections
import difflib
import os
import re

LATIN = re.compile(r"[A-Za-z]")
FILLERS = {"а", "аа", "э", "ээ", "эээ", "э-э", "э-э-э", "а-а", "мм", "ммм", "ну", "вот", "ага", "угу"}
GLOSSARY = {"opencode", "kilocode", "codex", "claude", "cursor", "references", "troubleshooting",
            "grace", "mcp", "skill", "skills", "prompt", "prompts", "reasoning", "agent", "agents", "api", "cli",
            "sdd", "bdd", "python", "typescript", "gemini", "qwen", "kimi", "deepseek", "gpt", "sqlite", "wip",
            "rules", "табуляц", "табуляции", "промт", "промпт", "промты", "промпты", "калькулятор", "калькулятора",
            "спецификация", "спецификации", "спецификаций", "агент", "агента", "агенты", "агентов", "контекст",
            "оркестратор", "песочница", "память", "сессия", "сессии", "скилл", "скиллы", "скиллов", "репозиторий",
             "коммит", "тесты", "тестер", "скептик", "аналитик", "архитектор", "библиотекарь", "фича", "дифф"}

PARAGRAPH_MAX_CHARS = 900
PARAGRAPH_MIN_CHARS = 300
PARAGRAPH_MAX_SENTENCES = 7
PARAGRAPH_BREAK = re.compile(
    r"^(?:И|А|Но|Однако|Поэтому|То есть|Теперь|Далее|Потом|Затем|Кроме того|В итоге|Если|Для|Например|Вообще|Ключевая|Основная|Следующий|Первый)\b",
    re.IGNORECASE,
)


def split_sentences(text):
    normalized = re.sub(r"\s+", " ", text or "").strip()
    if not normalized:
        return []
    parts = re.split(r"(?<=[.!?…])\s+(?=[А-Яа-яЁёA-Za-z0-9«\"'(])", normalized)
    result = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= PARAGRAPH_MAX_CHARS:
            result.append(part)
            continue
        words = part.split()
        chunk = []
        length = 0
        for word in words:
            extra = len(word) + (1 if chunk else 0)
            if chunk and length + extra > PARAGRAPH_MAX_CHARS:
                result.append(" ".join(chunk))
                chunk = []
                length = 0
                extra = len(word)
            chunk.append(word)
            length += extra
        if chunk:
            result.append(" ".join(chunk))
    return result


def paragraphize(text):
    paragraphs = []
    current = []
    length = 0
    for sentence in split_sentences(text):
        separator = 1 if current else 0
        if current and (
            length + separator + len(sentence) > PARAGRAPH_MAX_CHARS
            or len(current) >= PARAGRAPH_MAX_SENTENCES
            or (length >= PARAGRAPH_MIN_CHARS and PARAGRAPH_BREAK.match(sentence))
        ):
            paragraphs.append(" ".join(current))
            current = []
            length = 0
            separator = 0
        current.append(sentence)
        length += separator + len(sentence)
    if current:
        paragraphs.append(" ".join(current))
    return "\n\n".join(paragraphs) + ("\n" if paragraphs else "")


def tokens(text):
    """Слово вместе с прилипшей пунктуацией; нормализованная форма — для сопоставления."""
    return re.findall(r"\S+", (text or "").strip())


def norm(tok):
    return re.sub(r"^[^\w]+|[^\w]+$", "", tok.lower(), flags=re.UNICODE)


def susp(tok):
    """Подозрительный токен: склейка («известныевизвимости»), каша («Cash Hogeng Face»), обрубок."""
    n = norm(tok)
    if not n:
        return False
    if n in FILLERS:
        return True
    if len(n) > 18 and not LATIN.search(n):
        return True
    if len(n) > 8 and not re.search(r"[аеёиоуыэюяaeiou]", n):
        return True
    if LATIN.search(n) and re.search(r"[а-яё]", n):     # латиница и кириллица в одном слове
        return True
    return bool(re.search(r"(.)\1\1", n))               # тройной повтор букв


def score(tok):
    n = norm(tok)
    s = 0.0
    if LATIN.search(n):
        s += 2.0
    if n in GLOSSARY:
        s += 1.5
    elif any(n.startswith(g) for g in ("табуляц", "пром", "калькул", "специф", "оркестр", "песочниц", "репозитор")):
        s += 0.7
    if susp(tok):
        s -= 2.0
    return s


def parse(path):
    """Убирает служебную шапку целиком: строки заголовка и списка, затем пустые."""
    lines = open(path, encoding="utf-8").read().split("\n")
    i = 0
    while i < len(lines) and (not lines[i].strip() or lines[i].lstrip().startswith(("#", "- "))):
        i += 1
    return " ".join(lines[i:])


# R7: известные транслитерации → исходное написание. Строится по корпусу, каждая замена в журнале.
TRANSLIT = {"опен код": "OpenCode", "опенкод": "OpenCode", "опин код": "OpenCode", "опин-код": "OpenCode",
            "опин коды": "OpenCode", "опен кода": "OpenCode", "килокод": "KiloCode", "кило код": "KiloCode",
            "килокоды": "KiloCode", "кило": "Kilo", "грейс": "Grace"}


def postprocess(text, log, name):
    """R7: транслитерации обратно в латиницу; R8: косметика пунктуации (в журнал не пишется)."""
    for ru, en in TRANSLIT.items():
        if re.search(rf"\b{re.escape(ru)}\b", text, re.IGNORECASE):
            n = len(re.findall(rf"\b{re.escape(ru)}\b", text, re.IGNORECASE))
            text = re.sub(rf"\b{re.escape(ru)}\b", en, text, flags=re.IGNORECASE)
            log.append((name, "R7 транслитерация → латиница", ru, en, f"заменено {n} раз"))
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"([—–-])\s*\1+", r"\1", text)          # двойные тире
    text = re.sub(r"\s+([,.;:!?])", r"\1", text)            # пробел перед знаком
    text = re.sub(r"\(\s*\)", "", text)                    # пустые скобки
    return text.strip()


def fuse(canon_text, second_text, log, name, flag):
    c, g = tokens(canon_text), tokens(second_text)
    sm = difflib.SequenceMatcher(None, [norm(x) for x in g], [norm(x) for x in c], autojunk=False)
    ops = sm.get_opcodes()
    merged, i = [], 0
    while i < len(ops):                              # регионы: различия, разделённые коротким равным токеном, — одно целое
        op = ops[i]
        if (op[0] != "equal" and i + 2 < len(ops) and ops[i + 1][0] == "equal"
                and (ops[i + 1][2] - ops[i + 1][1]) <= 2 and ops[i + 2][0] != "equal"):
            merged.append(("replace", op[1], ops[i + 2][2], op[3], ops[i + 2][4]))
            i += 3
            continue
        merged.append(op)
        i += 1
    out = []
    for tag, i1, i2, j1, j2 in merged:
        if tag == "equal":
            # форма берётся у GigaAM: у неё пунктуация и заглавные; если он пуст — у канона
            out += [g[i] if norm(g[i]) else c[j] for i, j in zip(range(i1, i2), range(j1, j2))]
        elif tag == "delete":
            keep = [t for t in g[i1:i2] if norm(t) and norm(t) not in FILLERS]
            dropped = [t for t in g[i1:i2] if norm(t) in FILLERS or not norm(t)]
            out += keep
            if dropped:
                log.append((name, "R3 филлеры GigaAM убраны", " ".join(dropped), "", "убрано"))
            if keep:
                log.append((name, "только у GigaAM", " ".join(keep), "", "принято"))
        elif tag == "insert":
            out += c[j1:j2]
            log.append((name, "только у канона", "", " ".join(c[j1:j2]), "принято"))
        else:
            gs, cs = sum(score(x) for x in g[i1:i2]), sum(score(x) for x in c[j1:j2])
            g_txt, c_txt = " ".join(g[i1:i2]), " ".join(c[j1:j2])
            unsure = (abs((i2 - i1) - (j2 - j1)) >= 2
                      or any(susp(x) for x in g[i1:i2] + c[j1:j2])
                      or ((i2 - i1) + (j2 - j1) >= 3 and abs(gs - cs) < 0.5))
            if unsure:
                why = ("длина расходится" if abs((i2 - i1) - (j2 - j1)) >= 2
                       else "подозрительное слово" if any(susp(x) for x in g[i1:i2] + c[j1:j2])
                       else "близкие баллы")
                fid = len(flag) + 1
                flag.append({"id": fid, "file": name, "gigaam": g_txt, "canon": c_txt, "why": why,
                             "ctx_gigaam": " ".join(g[max(0, i1 - 10):i2 + 10]),
                             "ctx_canon": " ".join(c[max(0, j1 - 10):j2 + 10])})
                out.append(f"⟦F{fid:03d}⟧")
                log.append((name, "R6 решение отложено агенту", g_txt, c_txt, f"⟦F{fid:03d}⟧"))
            elif gs > cs:
                out += g[i1:i2]
                log.append((name, "R1/R2 GigaAM лучше", g_txt, c_txt, "принято от GigaAM"))
            elif cs > gs:
                out += c[j1:j2]
                log.append((name, "R1/R2 канон лучше", g_txt, c_txt, "принято от канона"))
            else:
                out += c[j1:j2]
                log.append((name, "R5 ничья → канон", g_txt, c_txt, "принято от канона"))
    return postprocess(" ".join(out), log, name)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--canon", required=True)
    ap.add_argument("--second", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    files = sorted(f for f in os.listdir(a.canon) if f.endswith(".txt"))
    log, flag, rows = [], [], []
    for f in files:
        cp, gp = os.path.join(a.canon, f), os.path.join(a.second, f)
        if not os.path.exists(gp):
            print("нет второго голоса:", f)
            continue
        fused = fuse(parse(cp), parse(gp), log, f.replace(".txt", ""), flag)
        output_name = os.path.splitext(f)[0] + ".md"
        open(os.path.join(a.out, output_name), "w", encoding="utf-8").write(paragraphize(fused))
        rows.append((output_name, len(tokens(fused))))
        print(f"{output_name[:40]:42} → {len(tokens(fused)):5} токенов", flush=True)

    rules = collections.Counter(x[1] for x in log)
    rep = ["# Журнал фьюзинга расшифровок", "",
           "Каждое расхождение моделей разрешено правилом; ничья отдана канону. Ничего не решено «на глаз».", "",
           "## Сколько решений каким правилом", "", "| правило | решений |", "|---|---|"]
    rep += [f"| {k} | {v} |" for k, v in rules.most_common()]
    rep += ["", f"**Отложено агенту на решение (спорные регионы): {len(flag)}**", "",
            "Каждый обозначен в тексте плейсхолдером `⟦F0NN⟧`. Решение принимает агент (LLM),",
            "с записью обоснования в журнал; человека в контуре нет.", ""]
    if flag:
        rep += ["| № | файл | GigaAM | канон | почему спорно |", "|---|---|---|---|---|"]
        rep += [f"| {x['id']} | {x['file']} | {x['gigaam']} | {x['canon']} | {x['why']} |" for x in flag]
        import json as _json
        _json.dump(flag, open(os.path.join(a.out, "flagged.json"), "w", encoding="utf-8"),
                   ensure_ascii=False, indent=1)
        rep += ["", f"Задания агенту выгружены: `flagged.json` ({len(flag)} записей, с контекстом ±10 слов)."]
    rep += ["", "## Все решения", "", "| файл | правило | GigaAM | канон | что взято |", "|---|---|---|---|---|"]
    rep += [f"| {n} | {r} | {g or '—'} | {c or '—'} | {d} |" for n, r, g, c, d in log]
    open(os.path.join(a.out, "fusion-report.md"), "w", encoding="utf-8").write("\n".join(rep) + "\n")
    print(f"\nрешений: {len(log)} | правилами: {dict(rules)} | помечено слушать: {len(flag)}")
    print("журнал:", os.path.join(a.out, "fusion-report.md"))


if __name__ == "__main__":
    main()
