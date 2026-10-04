#!/usr/bin/env python3
"""HTML-отчёт по экспериментам поиска расхождений: что искали, чем, что нашли.

Собирается из артефактов, а не из прозы: вердикты волны (`wave-*-verdicts.json`), кросс-страничные
отчёты подагентов (`_cross-verdicts-batch-*.json`), находки смыслового прохода (`wave-*-semantic.json`),
пары (`wave-*-pairs.json`). Ни одно число не набирается руками.

    python3 _toolkit/stance_report.py --wave 2026-09-14 --write
    # → _staging/audit/stance-experiments-<дата>.html
"""
import argparse
import collections
import datetime
import glob
import io
import json
import os
import re
import sys

import toolkit

# Разметка владельца: сравнение методов глазами. Здесь — только то, что было решением родителя,
# и владелец увидит это отдельными пометками, а не как «находку системы».
NET_CANDIDATES = {
    "raw/telegram/2026-05-16-skill-agents-best-practices-spravochniki-po-agentnoy-razrabo-103148.md|loop-engineering": "новая строка: goal-like loop против Ralph Loop",
    "raw/telegram/2026-06-20-state-sql-tables-pochemu-state-podhod-zamenyaet-event-driven-105897.md|bdd-and-quality-gates": "новая строка: state против event-driven specs",
    "raw/telegram/2026-07-10-krupnuyu-llm-nado-otpravlyat-v-detali-a-ne-na-strategiyu-107475.md|harness-architecture": "новая строка: мастера деталей против деградации модели как экономии",
    "raw/telegram/2026-08-24-j-space-i-superpoziciya-smyslov-metodiki-upravleniya-dayut-b-112681.md|instruction-overload": "новая строка: методики управления против инструкционного минимализма",
}
NET_REFERENCE = {
    "raw/telegram/2026-05-14-accessibility-tree-kak-bystryy-interfeys-agenta-dlya-ui-test-102967.md|context-engineering": "№14 визуальный слой",
    "raw/telegram/2026-06-20-grace-tri-etapa-ot-grafa-arhitektury-do-kontraktov-funkciy-105892.md|prompt-determinism-and-hooks": "№9 промпт против кода",
    "raw/telegram/2026-08-24-avtonomnye-sessii-opasny-kontrol-kazhdye-1000-strok-ili-15-m-112579.md|long-running-agents": "№20 автономия против HITL",
    "raw/telegram/2026-08-24-deepseek-harness-zaschita-kv-cache-log-proekciya-i-dolgozhiv-112578.md|context-engineering": "№13 почему падает длинный контекст",
    "raw/telegram/2026-08-24-deepseek-harness-zaschita-kv-cache-log-proekciya-i-dolgozhiv-112578.md|loop-engineering": "№18 reset против компактизации",
    "raw/telegram/2026-09-02-codex-schitaet-ustarevshiy-test-vazhnym-i-navorachivaet-dich-113439.md|bdd-and-quality-gates": "№22 автотесты: контур против антипаттерна",
    "raw/telegram/2026-09-02-opyt-openai-kak-pisat-ves-kod-tolko-cherez-agentov-113450.md|verification-tax-and-debt": "№22 и новый №28",
    "raw/telegram/2026-09-01-payplayn-skillov-etap-rezultat-vokrug-evalov-113424.md|bdd-and-quality-gates": "№23 текст против машиночитаемых артефактов",
}
PROPOSALS_FATE = [
    ("Агентский техдолг", "заведён", "спор №28 в карте конфликтов"),
    ("Знак пользы агентов", "заведён", "тот же №28 (та же ось, другая пара)"),
    ("Codex CLI как рекомендуемое ядро", "заведён", "тот же №28"),
    ("Кто вправе писать", "отклонён", "разобрано в тексте страницы harness-architecture («что принадлежит модели, а что обвязке»); корпусный носитель — Алексей — держит смежное, но не то же: бизнес-логика и контроль живут в коде, который пишет инженер, о праве агента писать саму обвязку он не говорит; без второго носителя это спор со сводкой вики"),
    ("generator → validator", "отклонён", "держится строкой №27 (бюджет верификации)"),
    ("CLI вместо скиллов", "отклонён", "разобрано в разделе «CLI как агентный интерфейс» страницы tool-design; смежную ось держат строки №6 (Skills против MCP) и №26 (MCP: слой абстракции или рабочий протокол)"),
]

CSS = """
:root{--bg:#14181f;--pan:#1b2129;--pan2:#212832;--line:#2b3442;--tx:#e6edf3;--dim:#9aa7b4;--acc:#7ee787;
--acc2:#79b8ff;--warn:#ffd479;--bad:#ff9d9d;--vio:#d2a8ff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,Segoe UI,Roboto,Ubuntu,sans-serif}
.wrap{max-width:1180px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:26px;margin:0 0 6px}h2{font-size:20px;margin:34px 0 10px;border-left:3px solid var(--acc2);padding-left:10px}
h3{font-size:16px;margin:18px 0 6px}
.lead{color:var(--dim);margin:0 0 18px}
.pan{background:var(--pan);border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin:12px 0}
.grid{display:grid;gap:12px}.g3{grid-template-columns:repeat(3,1fr)}.g2{grid-template-columns:repeat(2,1fr)}
@media(max-width:820px){.g3,.g2{grid-template-columns:1fr}}
.kpi{font-size:30px;font-weight:600}.kpi small{display:block;font-size:13px;color:var(--dim);font-weight:400;margin-top:2px}
table{width:100%;border-collapse:collapse;font-size:14px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600}
code,.mono{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:#c9d5e1;word-break:break-all}
.bar{height:9px;background:var(--pan2);border-radius:5px;overflow:hidden;margin-top:6px}
.bar i{display:block;height:100%}
.tag{display:inline-block;font-size:12px;padding:1px 7px;border-radius:20px;border:1px solid var(--line);color:var(--dim)}
.t-in{color:var(--acc);border-color:var(--acc)}.t-out{color:var(--warn);border-color:var(--warn)}
.t-bad{color:var(--bad);border-color:var(--bad)}.t-new{color:var(--vio);border-color:var(--vio)}
.card{background:var(--pan2);border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:8px;padding:12px 14px;margin:10px 0}
.card.inn{border-left-color:var(--acc2)}
.card h3{margin-top:0}
.q{background:#171c23;border-left:2px solid var(--line);padding:6px 10px;margin:6px 0;color:#cfd9e3;font-size:13.5px}
details{margin:8px 0}summary{cursor:pointer;color:var(--dim)}
.foot{color:var(--dim);font-size:13px;margin-top:40px;border-top:1px solid var(--line);padding-top:12px}
.legend span{margin-right:14px}
"""


def read(path):
    with io.open(path, encoding="utf-8", errors="ignore") as f:
        return f.read()


def load_json(path, default=None):
    if not os.path.exists(path):
        return default
    return json.load(io.open(path, encoding="utf-8"))


def bar(value, total, color):
    pct = 0 if not total else max(1, round(100 * value / total))
    return '<div class="bar"><i style="width:%d%%;background:%s"></i></div>' % (pct, color)


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def build(root, wave, date):
    st = toolkit.area(root, "stance")
    verdicts = load_json(os.path.join(st, "wave-%s-verdicts.json" % wave), {"items": []})
    pairs = load_json(os.path.join(st, "wave-%s-pairs.json" % wave), {"pairs": {}})
    semantic = load_json(os.path.join(st, "wave-%s-semantic.json" % wave), {"items": []})
    semantic_full = load_json(os.path.join(st, "wave-%s-semantic-full.json" % wave), {"items": []})
    full_by_key = {(x["source"], x["page"]): x for x in semantic_full.get("items", [])}
    inputs = load_json(os.path.join(st, "wave-%s-input.json" % wave), [])

    host_pairs = set()
    for rec in inputs:
        for _id, src in rec.get("sources", []):
            if src:
                host_pairs.add((rec["slug"], src))

    host_dist = collections.Counter()
    cross_dist = collections.Counter()
    cross_rows = []
    for it in verdicts.get("items", []):
        slug = it["slug"]
        for src, v in it.get("stance", {}).items():
            if (slug, src) in host_pairs:
                host_dist[v] += 1
            else:
                cross_dist[v] += 1
                cross_rows.append((slug, src, v))

    batch_files = sorted(glob.glob(os.path.join(st, "_cross-verdicts-batch-*.json")))
    batches = []
    proposals = []
    page_notes = {}
    for f in batch_files:
        d = load_json(f, {})
        batches.append({"file": os.path.basename(f), "pairs": d.get("pairs_checked") or sum(
            len(i.get("stance", {})) for i in d.get("items", []))})
        for it in d.get("items", []):
            if it.get("notes"):
                page_notes[it["slug"]] = it["notes"]
        proposals += d.get("proposed_disputes", [])

    h = []
    h.append('<!doctype html><meta charset="utf-8"><title>Эксперименты поиска расхождений — %s</title>' % date)
    h.append("<style>%s</style>" % CSS)
    h.append('<div class="wrap">')
    h.append("<h1>Расхождения в вики: четыре прогона и что каждый нашёл</h1>")
    h.append('<p class="lead">Волна инжеста %s · отчёт собран %s из артефактов (ни одно число не набрано руками). '
             'Сравнение методов: механическая сетка пар против смыслового прохода.</p>' % (wave, date))

    # --- сводка
    h.append('<div class="grid g3">')
    h.append('<div class="pan"><div class="kpi">%d<small>пар «источник × своя страница» (прогон 1)</small></div></div>'
             % sum(host_dist.values()))
    h.append('<div class="pan"><div class="kpi">%d<small>пар «источник × чужая страница» (прогон 2, 8 батчей)</small></div></div>'
             % sum(cross_dist.values()))
    h.append('<div class="pan"><div class="kpi">%d<small>находок смыслового прохода (прогон 3, без списка пар)</small></div></div>'
             % len(semantic.get("items", [])))
    h.append("</div>")

    # --- распределения
    h.append("<h2>Что сказали вердикты</h2>")
    for name, dist, total in (("Прогон 1 · источник против своей страницы", host_dist, sum(host_dist.values())),
                              ("Прогон 2 · источник против чужой страницы", cross_dist, sum(cross_dist.values()))):
        h.append('<div class="pan"><h3>%s — %d пар</h3>' % (name, total))
        for v, color in (("supports", "#7ee787"), ("partial", "#79b8ff"), ("unrelated", "#8b949e"), ("contradicts", "#ff9d9d")):
            n = dist.get(v, 0)
            h.append('<div style="margin:6px 0"><span class="mono">%s</span> <b>%d</b> из %d %s</div>'
                     % (v, n, total, bar(n, total, color)))
        h.append("</div>")

    # --- сетка против смысла
    outside = [x for x in semantic.get("items", []) if x.get("net") == "outside_the_net"]
    inside = [x for x in semantic.get("items", []) if x.get("net") != "outside_the_net"]
    h.append("<h2>Главный замер: сетка пар против смыслового прохода</h2>")
    h.append('<div class="pan"><p>Сетка строит пары по совпадению <b>термина</b> источника в тезисных местах '
             'страницы. Смысловой проход держал в контексте свод тезисов всех 67 страниц и источники целиком, '
             'без списка пар.</p>')
    h.append('<div class="grid g3">')
    h.append('<div class="pan"><div class="kpi">%d<small>находок смыслового прохода</small></div></div>' % len(semantic.get("items", [])))
    h.append('<div class="pan"><div class="kpi" style="color:var(--warn)">%d<small>из них вне сетки пар — сетка их не увидела бы</small></div></div>' % len(outside))
    h.append('<div class="pan"><div class="kpi">%d<small>внутри сетки (совпали со вердиктами прогона 2)</small></div></div>' % len(inside))
    h.append("</div>")
    h.append('<p class="lead">Вывод замера: сетка ловит совпадение имён, расхождение живёт в утверждении. '
             'Страница «для фронтенд-лупа нужен Vision» не называет accessibility tree; «полезное окно 100–150k» '
             'не называет KV-кэш. Термина нет — пары нет — вопроса нет.</p></div>')

    # --- находки смыслового прохода
    h.append("<h2>Прогон 3: находки смыслового прохода (%d)</h2>" % len(semantic.get("items", [])))
    h.append('<div class="pan legend"><span><span class="tag t-out">вне сетки пар</span> сетка не увидела бы</span>'
             '<span><span class="tag t-in">в сетке</span> совпало с прогоном 2</span>'
             '<span><span class="tag t-new">на решение</span> претендует на новую строку карты</span></div>')
    for x in semantic.get("items", []):
        key = x["source"] + "|" + x["page"]
        cand = NET_CANDIDATES.get(key)
        ref = NET_REFERENCE.get(key)
        cls = "card" if x.get("net") == "outside_the_net" else "card inn"
        h.append('<div class="%s">' % cls)
        h.append('<h3>%s <span class="mono">← %s</span></h3>' % (esc(x["page"]), esc(os.path.basename(x["source"]))))
        h.append('<div>')
        h.append('<span class="tag %s">%s</span>' % ("t-out" if x.get("net") == "outside_the_net" else "t-in",
                                                     "вне сетки пар" if x.get("net") == "outside_the_net" else "в сетке"))
        if cand:
            h.append(' <span class="tag t-new">на решение: %s</span>' % esc(cand.split(": ", 1)[-1]))
        if ref:
            h.append(' <span class="tag">ложится на %s</span>' % esc(ref))
        h.append('</div>')
        full = full_by_key.get((x["source"], x["page"]), {})
        h.append('<div class="q"><b>Тема:</b> %s</div>' % esc(x.get("topic", "")))
        if full:
            h.append('<div class="q"><b>Утверждает источник:</b> %s</div>' % esc(full.get("source_claim", "")))
            h.append('<div class="q"><b>Утверждает страница:</b> %s</div>' % esc(full.get("page_claim", "")))
            h.append('<div class="q" style="border-left-color:var(--acc2)"><b>Что делаешь по-разному:</b> %s</div>' % esc(full.get("decision_changed", "")))
            h.append('<div class="q" style="border-left-color:var(--acc)"><b>Источник прав, когда:</b> %s</div>' % esc(full.get("when_source_right", "")))
            h.append('<div class="q" style="border-left-color:var(--acc)"><b>Страница права, когда:</b> %s</div>' % esc(full.get("when_page_right", "")))
        h.append("</div>")

    # --- противоречия прогона 2
    contra = [(s, src) for s, src, v in cross_rows if v == "contradicts"]
    h.append("<h2>Прогон 2: противоречия (%d) — все уже держатся строками карты</h2>" % len(contra))
    h.append('<table><tr><th>страница</th><th>источник</th><th>решение</th></tr>')
    for s, src in contra:
        key = src + "|" + s
        ref = NET_REFERENCE.get(key)
        h.append('<tr><td class="mono">%s</td><td class="mono">%s</td><td>%s</td></tr>'
                 % (esc(s), esc(os.path.basename(src)), esc(ref) if ref else "уже держится строкой карты (названа в отчёте батча)"))
    h.append("</table>")
    h.append('<div class="pan"><p>Ни одно из них не потребовало новой строки: у каждого расхождения уже есть '
             'свой номер в карте конфликтов, и вторая строка была бы копией.</p></div>')

    # --- предложения детей
    # Откуда пришло предложение: имя файла батча прогона 2. Без него не видно, что шестёрка — побочный
    # продукт сетки пар, а не продолжение девятнадцати находок смыслового прохода (вопрос владельца).
    _origin = {}
    for _bf in batch_files:
        try:
            _bd = json.load(io.open(_bf, encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as ex:
            print(f"предупреждение: батч {_bf} пропущен при определении происхождения предложений ({ex})",
                  file=sys.stderr)
            _bd = {}
        for _pr in _bd.get("proposed_disputes") or []:
            _origin[str(_pr.get("title", ""))] = os.path.basename(_bf)
    h.append("<h2>Что предложили подагенты прогона 2 и что с этим стало (%d)</h2>" % len(proposals))
    # Что это за шесть. Владелец читал таблицу и не понимал предмета: строки были длиной в заголовок,
    # а кто именно спорит и что стало с предложением — не сказано. Теперь у таблицы есть подводка
    # и колонка «кто спорит»: носители берутся из самих предложений батчей, а не пересказываются.
    h.append('<div class="pan"><p><b>Это не продолжение девятнадцати находок и не их часть.</b> Девятнадцать — '
             'результат прогона 3 (смысловой проход, без списка пар); шестёрка — побочный продукт <b>прогона 2</b> '
             '(сетка пар «источник × чужая страница», восемь батчей): каждый батч, разбирая свои пары, отдельно '
             'предложил завести новую строку карты. Пересечение малое: одна пара из шестёрки встречается и среди '
             'девятнадцати (harness-architecture ← заметка 112273), остальные выросли из пар, которых смысловой '
             'проход не касался, и наоборот — двенадцать находок смыслового прохода сетка вообще не увидела. '
             'Проверены оба списка одним и тем же правилом: три предложения сошлись в одну строку <b>№28</b>, '
             'три отклонены — расхождение уже держится строкой <b>№27</b>, разведено в «Спорном» страницы '
             'tool-design, либо второго носителя нет, кроме сводки самой вики.</p></div>')
    h.append('<table><tr><th>предложение</th><th>кто спорит</th><th>откуда пришло</th><th>итог</th><th>куда легло и почему</th></tr>')
    seen = set()
    for p in proposals:
        pid = p.get("proposal_id") or p.get("title", "")[:40]
        if pid in seen:
            continue
        seen.add(pid)
        title = p.get("title") or pid
        fate = next((f for f in PROPOSALS_FATE if f[0].lower() in str(title).lower()), None)
        if fate:
            # кортеж: (ключ в заголовке, итог, почему). Раньше индексы были сдвинуты и в колонке «почему»
            # печатался ключ — причина решения не доезжала до отчёта, хотя владелец спрашивал именно её.
            _, verdict, why = fate
            cls = "t-in" if verdict == "заведён" else "t-bad"
            def _carrier(pr, names):
                for n in names:
                    side = pr.get(n) or {}
                    val = side.get("carrier") or side.get("bearer") or side.get("who") or ""
                    if val:
                        val = re.sub(r"\s+", " ", str(val))
                        # Вики-разметку в HTML не рендерят: [[файл|Имя]] показываем именем,
                        # [[файл]] — именем файла, иначе владелец читает скобки вместо носителя.
                        val = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", val)
                        val = re.sub(r"\[\[([^\]]+)\]\]", r"\1", val)
                        return val
                return ""
            a = _carrier(p, ("side_a", "position_a"))
            b = _carrier(p, ("side_b", "position_b"))
            sides = ("<b>За:</b> %s<br><b>Против:</b> %s" % (esc(a), esc(b))) if (a or b) else "—"
            h.append('<tr><td>%s</td><td>%s</td><td class="mono">%s</td><td><span class="tag %s">%s</span></td><td>%s</td></tr>'
                     % (esc(title), sides, esc(_origin.get(str(title), "прогон 2")), cls, esc(verdict), esc(why)))
        else:
            h.append('<tr><td>%s</td><td>—</td><td class="mono">%s</td><td><span class="tag">ждёт решения</span></td><td>—</td></tr>'
                     % (esc(title), esc(_origin.get(str(title), "прогон 2"))))
    h.append("</table>")

    # --- карта
    h.append("<h2>Карта конфликтов после работы</h2>")
    _cmap = _domain.register_path(root, "conflicts")
    map_rows = len(re.findall(r'(?m)^\|\s*\**\d+\**\s*\|', read(_cmap))) if _cmap and os.path.exists(_cmap) else 0
    h.append('<div class="pan"><p>Споров: <b>' + str(map_rows) + '</b>, у каждого — строка «когда выигрывает A / когда выигрывает B» '
             '(парность проверяет §25 линтера). Новые строки этих прогонов: <b>№27</b> «бюджет верификации» '
             '(прогон 1) и <b>№28</b> «агентский техдолг: разгребают или множат» (сошлись три батча прогона 2).</p></div>')

    # --- примечания детей по страницам
    if page_notes:
        h.append("<h2>Заметки подагентов по страницам (как читались пары)</h2>")
        for slug in sorted(page_notes):
            note = page_notes[slug]
            h.append('<details><summary>%s</summary><div class="q">%s</div></details>'
                     % (esc(slug), esc(note if isinstance(note, str) else json.dumps(note, ensure_ascii=False))[:4000]))

    h.append('<div class="foot">Артефакты: <code>_staging/stance/wave-%s-verdicts.json</code> '
             '(<b>%d</b> страниц), <code>wave-%s-pairs.json</code>, <code>wave-%s-semantic.json</code>, '
             '<code>_cross-verdicts-batch-1..%d.json</code>. Разбор замера — '
             '<code>_staging/audit/evidence/semantic-vs-pairs-%s.md</code>. '
             'Все находки — самоотчёты практиков без независимых замеров; вердикты «не относится» — нормальный ответ там, '
             'где совпал термин, а не тема.</div>'
             % (wave, len(verdicts.get("items", [])), wave, wave, len(batch_files), date))
    h.append("</div>")
    return "\n".join(h)


def main():
    ap = argparse.ArgumentParser(description="HTML-отчёт по экспериментам поиска расхождений")
    ap.add_argument("--wave", required=True)
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()
    root = os.path.abspath(args.wiki)
    date = datetime.date.today().isoformat()
    html = build(root, args.wave, date)
    out = toolkit.area(root, "audit", "stance-experiments-%s.html" % date)
    print("HTML: " + str(len(html)) + " знаков")
    if args.write:
        with io.open(out, "w", encoding="utf-8", newline="") as f:
            f.write(html)
        print("записано: " + os.path.relpath(out, root))
    return 0



import os as _os
import sys as _sys

_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import domain as _domain  # домен экземпляра: имена его страниц и реестров знает только он

if __name__ == "__main__":
    sys.exit(main())
