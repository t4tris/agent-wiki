#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""HTML-обзор потерь: сорок две тонкие записи корпуса против вики.

Владелец 2026-09-17: «мне надо предварительно оценить потери информации при предложенной процедуре.
Создай html со всеми 42 записями-кандидатами на удаление, и напротив каждой — какая информация из
этой карточки уже содержится в вики». Собирает данные из `_staging/delete-review/notes-42.json`
(тексты записей, их места в вики) и `_staging/delete-review/verdicts-*.json` (оценки читателей).

    python3 _toolkit/delete_review_html.py --wiki .
"""
import argparse
import glob
import html
import io
import json
import os
import re
import sys

import toolkit

# механика владельца: галочки по строкам + кнопка «экспорт в JSON» (как в candidates_review.py и intents_review.py)
SCRIPT = """<script>
const KEY = 'delete-thin-notes-approval-v1';
function boxes(){ return Array.from(document.querySelectorAll('input.ok')); }
function save(){ const s = {}; boxes().forEach(b => s[b.dataset.stem] = b.checked);
  try { localStorage.setItem(KEY, JSON.stringify(s)); } catch(e){} cnt(); }
function load(){ let s = {}; try { s = JSON.parse(localStorage.getItem(KEY) || '{}'); } catch(e){}
  boxes().forEach(b => b.checked = !!s[b.dataset.stem]); cnt(); }
function all(v){ boxes().forEach(b => b.checked = v); save(); }
function cnt(){ const n = boxes().filter(b => b.checked).length;
  document.getElementById('cnt').textContent = ' отмечено: ' + n + ' из ' + boxes().length; }
function exp(){
  const items = boxes().map(b => ({ stem: b.dataset.stem, approved: b.checked }));
  if (!items.some(i => i.approved)) { alert('Ни одна запись не отмечена.'); return; }
  const data = { contract_version: '1.0', kind: 'delete-thin-notes', items: items };
  const blob = new Blob([JSON.stringify(data, null, 1)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'delete-thin-notes-approval.json';
  a.click();
}
boxes().forEach(b => b.addEventListener('change', save));
load();
</script>"""

# порядок: сначала то, что в вики не разобрано, в конце — то, что уже названо целиком
ORDER = {"в вики не разобрано": 0, "не содержится": 0, "частично": 1, "содержание на месте": 2, "полностью": 2}
BADGE = {"в вики не разобрано": "bad", "не содержится": "bad", "частично": "warn",
         "содержание на месте": "ok", "полностью": "ok"}

CSS = """
:root{--bg:#14181f;--pan:#1b2129;--pan2:#212832;--line:#2b3442;--tx:#e6edf3;--dim:#9aa7b4;
--acc:#7ee787;--acc2:#79b8ff;--warn:#e3b341;--bad:#ff7b72;}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 -apple-system,Segoe UI,Roboto,sans-serif}
.wrap{max-width:1400px;margin:0 auto;padding:28px 20px 80px}
.bar{position:sticky;top:0;z-index:5;background:var(--pan);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin:0 0 18px;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.bar button{background:var(--pan2);color:var(--tx);border:1px solid var(--line);border-radius:8px;padding:6px 12px;cursor:pointer;font-size:14px}
.bar button:hover{border-color:var(--acc2)}
.appr{display:flex;align-items:center;gap:6px;color:var(--dim);font-size:13px;margin-left:auto;margin-right:12px}
.appr input{width:16px;height:16px;accent-color:var(--acc)}

h1{font-size:24px;margin:0 0 6px}
h2{font-size:15px;margin:0;color:var(--dim);font-weight:500}
.lead{color:var(--dim);margin:14px 0 22px;max-width:1000px}
.stats{display:flex;gap:10px;flex-wrap:wrap;margin-bottom:22px}
.stat{background:var(--pan);border:1px solid var(--line);border-radius:10px;padding:10px 14px;min-width:130px}
.stat b{display:block;font-size:20px}
.stat span{color:var(--dim);font-size:12px}
.card{background:var(--pan);border:1px solid var(--line);border-radius:12px;margin:0 0 18px;overflow:hidden}
.card>header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;padding:12px 16px;
background:var(--pan2);border-bottom:1px solid var(--line);flex-wrap:wrap}
.card>header .id{font-family:ui-monospace,Consolas,monospace;font-size:13px;color:var(--acc2)}
.card>header .meta{color:var(--dim);font-size:12.5px}
.badge{font-size:12px;padding:3px 9px;border-radius:999px;border:1px solid var(--line);white-space:nowrap}
.badge.ok{color:#0f1115;background:var(--acc);border-color:var(--acc)}
.badge.warn{color:#0f1115;background:var(--warn);border-color:var(--warn)}
.badge.bad{color:#0f1115;background:var(--bad);border-color:var(--bad)}
.cols{display:grid;grid-template-columns:1fr 1.25fr;gap:0}
@media(max-width:1000px){.cols{grid-template-columns:1fr}}
.col{padding:14px 16px}
.col+.col{border-left:1px solid var(--line)}
@media(max-width:1000px){.col+.col{border-left:0;border-top:1px solid var(--line)}}
.col h3{font-size:12px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:0 0 10px}
.txt{white-space:pre-wrap;background:#0f1319;border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:13.5px}
.txt.block{margin-top:10px;border-left:3px solid var(--acc2)}
.txt.block .cap{color:var(--dim);font-size:11.5px;display:block;margin-bottom:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
td,th{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--dim);font-weight:500;font-size:11.5px;text-transform:uppercase;letter-spacing:.05em}
code{font-family:ui-monospace,Consolas,monospace;font-size:12.5px;color:var(--acc2)}
.q{color:var(--tx)}
.lost{margin:0}
.lost li{margin:0 0 6px}
.lost.none li{color:var(--acc)}
.ment{color:var(--dim);font-size:12.5px;margin-top:10px}
.ment code{color:var(--dim)}
"""


def esc(x):
    return html.escape(str(x or ""))


def load(path, default):
    if not os.path.exists(path):
        return default
    try:
        return json.load(io.open(path, encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as ex:
        print(f"предупреждение: {path} не прочитан как JSON ({ex}); использован пустой fallback", file=sys.stderr)
        return default


def main():
    ap = argparse.ArgumentParser(description="HTML-обзор потерь по тонким записям корпуса")
    ap.add_argument("--wiki", default=".")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    data = load(toolkit.area(root, "delete-review", "notes-42.json"), [])
    verdicts = {}
    # разбор по понятиям (`*-concept.json`) перекрывает первый, буквальный заход: тот искал слова,
    # а владелец 2026-09-17 показал на записи 114014, что искать надо понятия (воркер, спека, стек)
    files = (sorted(glob.glob(toolkit.area(root, "delete-review", "verdicts-*-concept.json"))) or
             sorted(glob.glob(toolkit.area(root, "delete-review", "verdicts-*.json"))))
    for f in files:
        for v in load(f, []):
            verdicts[v.get("stem", "")] = v
    # План удаления собирает машина (`thin_notes.py`), и обзор обязан показывать ровно его строки:
    # до 2026-09-17 план был собран разово руками, и семь записей в нём уже не были тонкими.
    THIN_PLAN = set()
    plan_path = toolkit.area(root, "delete-thin-notes.tsv")
    if os.path.exists(plan_path):
        with io.open(plan_path, encoding="utf-8") as f:
            for line in f.read().rstrip("\n").split("\n")[1:]:
                if line.split("\t")[0].strip():
                    THIN_PLAN.add(line.split("\t")[0].strip())
    rows = []
    for n in data:
        if THIN_PLAN and n["stem"] not in THIN_PLAN:
            continue
        v = verdicts.get(n["stem"], {})
        rows.append((ORDER.get(v.get("verdict", ""), 3), n, v))
    rows.sort(key=lambda r: (r[0], -int(r[1]["useful"])))
    cnt = {}
    for _o, _n, v in rows:
        k = v.get("verdict", "нет оценки")
        cnt[k] = cnt.get(k, 0) + 1
    # Числа в шапке считаются по отрисованному списку, а не пишутся словами: «сорок две» в заголовке
    # пережили план на 42 записи и врали на семи строках, которых в плане уже не было (2026-09-17).
    total_useful = sum(r[1]["useful"] for r in rows)
    shown = len(rows)
    # Пилюля вердикта врала: владелец 2026-09-17 показал две строки подряд (Awesome-Graph-Engineering,
    # Printing Press), где читатель писал «в вики не разобрано», а ресурс назван на страницах. Поэтому статус
    # в листе больше не вердикт, а измерение: сколько страниц называют ресурс в ТЕЛЕ (шапка `sources:` не
    # считается — она уходит вместе с записью) и какая доля текста записи найдена на страницах дословно.
    # Читательское суждение остаётся рядом отдельной строкой, помеченное как слово читателя.
    wiki_pages = {}
    for _path in sorted(glob.glob(toolkit.wiki(root, "**", "*.md"), recursive=True)):
        _rel = os.path.relpath(_path, root).replace("\\", "/")
        if _rel.startswith("wiki/sources/"):
            continue                      # зеркала доказательством не считаются: они уходят вместе с записью
        with io.open(_path, encoding="utf-8", errors="replace") as _f:
            _text = _f.read()
        _text = re.sub(r"(?ms)^sources:.*?\]", "", _text)
        wiki_pages[_rel.replace("wiki/", "")] = _text
    _wiki_blob = " \n ".join(wiki_pages.values())

    def measure(n):
        """Измерение покрытия: страницы, называющие ресурс, и доля дословно найденного текста."""
        body = n.get("body", "") or ""
        url = (n.get("url") or "").rstrip("/")
        tail = url.split("/")[-1] if url else ""
        host = re.sub(r"^www\.", "", url.split("//")[-1].split("/")[0]) if url else ""
        core = host.split(".")[0] if host else ""
        names = re.findall(r"\*\*([^*]{3,60})\*\*", body)
        needles = [x.lower() for x in ([tail] if len(tail) >= 5 and "." not in tail else []) + [core]
                   if len(x) >= 4]
        needles += [x.lower() for x in names if len(x) >= 5]
        named = []
        if needles:
            for rel, text in wiki_pages.items():
                low = text.lower()
                if any(nd in low for nd in needles):
                    named.append(rel)
        frags = [body[i:i + 50] for i in range(0, max(0, len(body) - 50), 25)]
        frags = [f for f in frags if len(f.strip()) > 35]
        found = sum(1 for f in frags if f in _wiki_blob)
        share = int(round(100.0 * found / len(frags))) if frags else 0
        return named, found, len(frags), share

    rows_measure = {}
    machine_fixed = 0

    out = ["<!doctype html><html lang='ru'><head><meta charset='utf-8'>",
           "<title>Потери при удалении тонких записей — обзор</title><style>" + CSS + "</style></head><body>",
           "<div class='wrap'>",
           "<h1>Что потеряется, если удалить %d тонких записей</h1>" % shown,
           "<h2>Обзор к процедуре «запись корпуса живёт, пока её содержание не импортировано» · 2026-09-17</h2>",
           "<div class='lead'>Слева — запись целиком, как она лежит в <code>raw/telegram</code> и в зеркале хранилища. "
           "Справа — что из её содержания уже сказано в вики (файл, раздел, дословный фрагмент) и что исчезнет вместе "
           "с записью. Статус строки считает машина: сколько страниц называют ресурс в теле (шапка <code>sources:</code> не считается) и какая доля текста записи найдена дословно. Суждение читателя приведено рядом отдельной строкой — как мнение, а не проверка. "
           "Зеркала <code>wiki/sources/</code> доказательством не считаются: они удаляются вместе с записью. "
           "Разбор идёт по понятиям, а не по словам: владелец показал на записи 114014, что буквальный поиск "
           "слова «Луна» не находит ничего, тогда как запись говорит про воркера — малую модель, которой для "
           "хорошей работы нужны спека и каноничный стек, — а эти понятия живут на странице "
           "<code>multi-agent-orchestration</code>.</div>",
           "<div class='stats'>",
           "<div class='stat'><b>%d</b><span>записей-кандидатов</span></div>" % shown,
           "<div class='stat'><b>%d</b><span>знаков полезного текста во всех</span></div>" % total_useful,
           "<div class='stat'><b>%d</b><span>содержание целиком в вики</span></div>" % cnt.get("полностью", 0),
           "<div class='stat'><b>%d</b><span>частично</span></div>" % cnt.get("частично", 0),
           "<div class='stat'><b>%d</b><span>в вики не содержится (слово читателей)</span></div>" % cnt.get("не содержится", 0),
           "<div class='stat'><b>%%FIXED%%</b><span>поправок машины к вердиктам</span></div>",
           "</div>"]
    out.append("<div class='bar'>"
               "<b>Утверждение удаления:</b> отметьте записи галочками и выгрузите файл — удаление "
               "запускается только по нему. <button onclick=\"all(true)\">отметить все</button> "
               "<button onclick=\"all(false)\">снять все</button> "
               "<button onclick=\"exp()\">экспорт утверждения в JSON</button> "
               "<span id='cnt'></span></div>")
    for _o, n, v in rows:
        verdict = v.get("verdict", "нет оценки")
        named, found, total_frags, share = measure(n)
        if verdict in ("в вики не разобрано", "не содержится") and (named or share >= 30):
            machine_fixed += 1
        out.append("<section class='card'><header>")
        out.append("<div><div class='id'>%s</div><div class='meta'>%s · %s · сообщение %s%s</div></div>"
                   % (esc(n["stem"]), esc(n["channel"]), esc(n["date"]), esc(n["id"]),
                      (" · " + esc(n["url"])) if n.get("url") else ""))
        out.append("<label class='appr'><input type='checkbox' class='ok' data-stem='%s'> <span>удалить</span></label>" % esc(n["stem"]))
        if named or share:
            out.append("<span class='badge %s'>машина: назван на %d стр., дословно %d%% текста</span>"
                       % ("ok" if share >= 30 else "warn", len(named), share))
        else:
            out.append("<span class='badge bad'>машина: ни названия, ни дословных совпадений</span>")
        out.append("<div class='ment'>слово читателя (суждение, не проверка): «%s»%s</div>"
                   % (esc(verdict), (", дословно найдено " + str(found) + " из " + str(total_frags) + " фрагментов") if total_frags else ""))
        if named:
            out.append("<div class='ment'>называют ресурс: %s</div>"
                       % ", ".join("<code>%s</code>" % esc(x) for x in named[:5]))
        out.append("</header><div class='cols'>")
        # левая колонка — запись
        out.append("<div class='col'><h3>Запись (удаляется)</h3><div class='txt'>%s</div>" % esc(n["body"]))
        if n.get("block"):
            out.append("<div class='txt block'><span class='cap'>блок «Что за ссылкой» — тоже уходит вместе с записью</span>%s</div>"
                       % esc(n["block"]))
        if n.get("summary"):
            out.append("<div class='ment'>машинная шапка записи: %s</div>" % esc(n["summary"]))
        out.append("</div>")
        # правая колонка — вики
        out.append("<div class='col'><h3>Что уже в вики</h3>")
        if v.get("concepts"):
            out.append("<div class='ment'>понятия записи: " +
                       ", ".join("<b>%s</b>" % esc(x) for x in v["concepts"]) + "</div>")
        if v.get("landing"):
            out.append("<div class='ment'>куда просятся данные: %s</div>" % esc(v["landing"]))
        wheres = v.get("where") or []
        if wheres:
            out.append("<table><tr><th>Где</th><th>Что сказано</th></tr>")
            for w in wheres:
                sec = w.get("section") or ""
                out.append("<tr><td><code>%s</code>%s</td><td class='q'>%s</td></tr>"
                           % (esc(w.get("file")), ("<br><span class='ment'>%s</span>" % esc(sec)) if sec else "",
                              esc(w.get("quote"))))
            out.append("</table>")
        elif v:
            out.append("<div class='ment'>в вики не нашлось ни одного места, где сказано то же: запись целиком новая для вики</div>")
        else:
            out.append("<div class='ment'>оценки нет: читатель по этой записи ещё не отчитался</div>")
        if v.get("comment"):
            out.append("<div class='ment'>%s</div>" % esc(v["comment"]))
        lost = v.get("lost") or []
        out.append("<h3 style='margin-top:14px'>Что потеряется</h3>")
        if lost:
            out.append("<ul class='lost'>" + "".join("<li>%s</li>" % esc(x) for x in lost) + "</ul>")
        else:
            out.append("<ul class='lost none'><li>ничего: содержание названо в вики целиком</li></ul>")
        refs = n.get("refs") or []
        if refs:
            places = sorted({r["where"] for r in refs})
            out.append("<div class='ment'>упоминаний записи в вики и карточках: %d, в файлах: %s</div>"
                       % (len(refs), ", ".join("<code>%s</code>" % esc(p) for p in places[:6])))
        out.append("</div></div></section>")
    out.append(SCRIPT)
    out.append("</div></body></html>")
    path = toolkit.area(root, "audit", "delete-thin-notes-review.html")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    html = "\n".join(out).replace("%%FIXED%%", str(machine_fixed))
    io.open(path, "w", encoding="utf-8", newline="").write(html)
    print("HTML: %d знаков" % len("\n".join(out)))
    print("записано: %s" % path.replace(os.sep, "/"))
    print("оценок получено: %d из %d" % (len(verdicts), len(data)))
    print("поправок машины к вердиктам: %d" % machine_fixed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
