#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Кандидаты в страницы: слежение за ростом упоминаний после решения владельца.

Зачем. Порог «2+ источника или 5+ страниц» — вопрос, а не приказ: решение по кандидату принимает владелец.
Но решение принимается на данных на сегодня, а следующий инджест добавляет упоминания. Владелец 2026-09-15:
«когда количество упоминаний страниц вырастет после следующего инджеста, я хочу видеть это, чтобы пересмотреть
своё решение о создании страниц». Этот лист показывает по каждому кандидату: сколько источников и страниц
упоминают его **сейчас**, сколько было **на момент решения** (снимок `candidates-baseline-*.json`) и насколько
выросло; сверху — те, кто вырос, то есть те, чьё решение стоит пересмотреть.

Данные и артефакт:
  * `_staging/candidate-decisions.tsv`                        — решения владельца (ключ, имя, решение, причина, дата);
  * `_staging/audit/candidates-baseline-2026-09-15.json`      — снимок упоминаний на момент решений;
  * `_staging/audit/candidates-status.html`                   — производный лист (этот генератор).

Запуск:
    python3 _toolkit/candidates_status.py --wiki .                 # разбор: кто вырос
    python3 _toolkit/candidates_status.py --wiki . --write         # записать HTML
    python3 _toolkit/candidates_status.py --wiki . --baseline      # снять снимок упоминаний (после решений)
"""
import argparse
import glob
import io
import json
import os
import sys

import toolkit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from slugify import slug_for


def read_queue_names(root):
    """Ключ -> самое полное имя понятия из очереди.

    В очереди имя несёт английский термин («контекстное окно (context window)»), а счёт кандидатов
    работает с коротким написанием из карточек — из-за этого лист показывал транслитерацию вместо термина.
    """
    names = {}
    for f in ("_staging/prose-candidates.tsv", "_staging/candidate-decisions.tsv"):
        path = os.path.join(root, f)
        if not os.path.exists(path):
            continue
        for line in io.open(path, encoding="utf-8").read().rstrip("\n").split("\n")[1:]:
            parts = line.split("\t")
            if len(parts) > 1 and parts[0].strip():
                names[parts[0].strip()] = parts[1].strip()
    return names


def page_name(name):
    """Имя страницы для листа: английский термин, а не транслитерация.

    Владелец 2026-09-17: «четыре понятия всё ещё кириллицей… нелепые слаги: stek вместо stack,
    planirovschik вместо planner». Лист обязан показывать то имя, которым страница будет создана:
    `slug_for` берёт английский термин из скобок, а без термина честно отказывает — вместо
    `kontekstnoe-okno` в листе появится «нужен английский термин».
    """
    try:
        return slug_for(name)
    except ValueError:
        return "нужен английский термин"
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import candidates as cand

BASELINE_GLOB = "audit/candidates-baseline-*.json"
OUT = "audit/candidates-status.html"
OUT_DISPLAY = os.path.join(toolkit.AREA, "audit", "candidates-status.html")


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def counts_for(vault, keys):
    """Упоминания по каждому ключу: источников (карточки) и страниц (упоминание в прозе)."""
    by_sources, _ = cand.card_terms(vault)
    by_pages, _, _ = cand.page_terms(vault)
    mention = cand.mention_counts(vault, list(keys))
    out = {}
    for k in keys:
        out[k] = {"sources": len(by_sources.get(k, ())),
                  "pages": max(len(by_pages.get(k, ())), mention.get(k, 0))}
    return out


def baseline_path(root):
    hits = sorted(glob.glob(toolkit.area(root, BASELINE_GLOB)))
    return hits[-1] if hits else None


SYNONYM_REASON = "полный синоним существующей страницы"


def synonym_of(vault, key, known_names):
    """Слаг страницы-синонима, если термин полностью покрыт существующей страницей."""
    s = cand.synonym_page(key, list(known_names))
    return s.replace(" ", "-") if s else ""


def build_rows(root, vault):
    live = {c["key"]: c for c in cand.candidates(vault, root)}
    _, _, known_names = cand.page_terms(vault)
    decisions = cand.load_decisions(root)
    base_file = baseline_path(root)
    base = json.load(open(base_file, encoding="utf-8")) if base_file else {}
    base_counts = base.get("counts", {})
    keys = set(live) | set(decisions) | set(base_counts)
    now = counts_for(vault, keys)
    rows = []
    for k in sorted(keys):
        c = live.get(k, {})
        dec, reason, date = decisions.get(k, ("", "", ""))
        b = base_counts.get(k, {})
        row = {
            "key": k,
            "name": c.get("name") or (b.get("name") or k),
            "sources": now[k]["sources"],
            "pages": now[k]["pages"],
            "src_was": b.get("sources"),
            "pages_was": b.get("pages"),
            "decision": dec,
            "reason": reason,
            "date": date or b.get("date", ""),
            "passes": bool(c),
            "from_prose": bool(c.get("from_prose")),
            "found": c.get("found") or "",
        }
        row["d_src"] = (row["sources"] - row["src_was"]) if row["src_was"] is not None else None
        row["d_pages"] = (row["pages"] - row["pages_was"]) if row["pages_was"] is not None else None
        row["synonym"] = (c.get("synonym") or synonym_of(vault, k, known_names)).replace(" ", "-")
        # причина, которую стоит уточнить: у кандидата есть полный синоним, а отказано по общей причине
        row["reason_suspect"] = bool(row["synonym"] and dec and reason != SYNONYM_REASON)
        row["revisit"] = bool(dec and (row["d_src"] or 0) + (row["d_pages"] or 0) > 0)
        rows.append(row)
    rows.sort(key=lambda r: (not (r["revisit"] or r["reason_suspect"]), r["decision"] != "отказ",
                             -(r["d_src"] or 0), r["key"]))
    return rows, base.get("taken"), base_file


def render(root, vault):
    queue_names = read_queue_names(root)          # имена очереди несут английский термин, счёт — короткую форму
    rows, taken, base_file = build_rows(root, vault)
    revisit = [r for r in rows if r["revisit"]]
    suspect = [r for r in rows if r["reason_suspect"]]
    decided = sum(1 for r in rows if r["decision"])
    undecided = [r for r in rows if not r["decision"]]

    def cells(r):
        def d(v):
            if v is None:
                return "<span class='was'>—</span>"
            if v > 0:
                return f"<span class='up'>+{v}</span>"
            if v < 0:
                return f"<span class='down'>{v}</span>"
            return "<span class='was'>0</span>"
        name_full = queue_names.get(r["key"], r["name"])
        was_s = "" if r["src_was"] is None else f" <span class='was'>было {r['src_was']}</span>"
        was_p = "" if r["pages_was"] is None else f" <span class='was'>было {r['pages_was']}</span>"
        dec = {"страница": "page", "отказ": "ref", "покрыто страницей": "cover"}.get(r["decision"], "")
        if r["decision"]:
            dec_html = f"<span class='dec {dec}'>{esc(r['decision'])}</span>"
        else:
            dec_html = "<span class='was'>решения нет</span>"
        pass_txt = "да" if r["passes"] else "<span class='was'>нет</span>"
        tr_cls = " ".join(x for x in ("revisit" if r["revisit"] else "",
                                      "suspect" if r["reason_suspect"] else "") if x)
        syn = f"<span class='syn'>{esc(r['synonym'])}</span>" if r["synonym"] else "<span class='was'>—</span>"
        reasons = "".join(f"<option value='{esc(x)}'{' selected' if x == r['reason'] else ''}>{esc(x)}</option>"
                          for x in cand.REASONS)
        mark = (f"<td class='mark'>"
                f"<select class='dec-sel'>"
                f"<option value='' selected>не менять</option>"
                f"<option value='page'>страница</option>"
                f"<option value='refusal'>отказ</option></select><br>"
                f"<div class='slug-auto' data-slug='{esc(page_name(name_full))}'>"
                f"имя страницы: {esc(page_name(name_full))}</div>"
                f"<select class='reason' disabled>{reasons}</select></td>")
        return (f"<tr class='{tr_cls}' data-key='{esc(r['key'])}' data-name='{esc(name_full)}'>"
                f"<td>{mark}</td>"
                f"<td><b>{esc(name_full)}</b>"
                + (f"<div class='prose' title='найден чтением страниц: {esc(r['found'])}'>назван прозой</div>"
                   if r.get("from_prose") else "")
                + f"<div class='key'>{esc(r['key'])}</div></td>"
                f"<td class='num'>{r['sources']}{was_s} {d(r['d_src'])}</td>"
                f"<td class='num'>{r['pages']}{was_p} {d(r['d_pages'])}</td>"
                f"<td>{dec_html}<div class='reason'>{esc(r['reason'])}</div></td>"
                f"<td>{syn}</td>"
                f"<td>{pass_txt}</td></tr>")

    table = ("<table><thead><tr><th>отметка</th><th>кандидат</th><th>источников</th><th>страниц</th>"
             "<th>решение владельца</th><th>синоним (если есть)</th><th>проходит порог сейчас</th></tr></thead><tbody>"
             + "".join(cells(r) for r in rows) + "</tbody></table>")
    stamp = (f"Снимок упоминаний на момент решений: <code>{esc(os.path.relpath(base_file, root)) if base_file else 'нет'}</code>"
             + (f" (снят {esc(taken)})" if taken else "")
             + f" · решения: <code>_staging/candidate-decisions.tsv</code> · порог схемы: {cand.THRESHOLD}+ источника "
               f"или {cand.PAGES_THRESHOLD}+ страниц · лист собран по репозиторию сейчас.")
    table_bar = ("<div class='bar'>"
                 "<input type='text' id='q' placeholder='фильтр по кандидату'>"
                 "<button id='allRef'>всем отказ</button>"
                 "<button id='resetMarks'>сбросить выбор</button>"
                 "<button class='main' id='export'>Выгрузить решения в JSON</button>"
                 "<span id='cnt'></span></div>")
    script = """<script>
function rows() { return [...document.querySelectorAll('tbody tr')]; }
function KEY() { return 'wiki-candidates-status-v1'; }
function mark(r) {
  const sel = r.querySelector('.dec-sel').value;
  // имя страницы считает машина (slugify): человеку вводить нечего
  r.querySelector('.reason').disabled = sel !== 'refusal';
}
function state() {
  const st = {};
  rows().forEach(r => { st[r.dataset.key] = {
    d: r.querySelector('.dec-sel').value,
    slug: (r.querySelector('.slug-auto') || {dataset:{}}).dataset.slug,
    reason: r.querySelector('.reason').value }; });
  return st;
}
function save() { try { localStorage.setItem(KEY(), JSON.stringify(state())); } catch (e) {} }
function load() {
  let st = null;
  try { st = JSON.parse(localStorage.getItem(KEY()) || 'null'); } catch (e) {}
  if (!st) return;
  rows().forEach(r => { const s = st[r.dataset.key]; if (!s) return;
    r.querySelector('.dec-sel').value = s.d || '';
  
    if (s.reason) r.querySelector('.reason').value = s.reason; });
}
function upd() {
  rows().forEach(mark);
  const chosen = rows().filter(r => r.querySelector('.dec-sel').value);
  const pages = chosen.filter(r => r.querySelector('.dec-sel').value === 'page').length;
  const refs = chosen.filter(r => r.querySelector('.dec-sel').value === 'refusal').length;
  document.getElementById('cnt').textContent =
    'выбрано решений: ' + chosen.length + ' · страница: ' + pages + ' · отказ: ' + refs;
  save();
}
function exportJson() {
  const items = [];
  rows().forEach(r => {
    const d = r.querySelector('.dec-sel').value;
    if (!d) return;
    const item = { term: r.dataset.name, decision: d, date: '2026-09-15' };
    if (d === 'page') item.slug = (r.querySelector('.slug-auto') || {dataset:{}}).dataset.slug;
    else item.reason = r.querySelector('.reason').value;
    items.push(item);
  });
  if (!items.length) { alert('Ничего не выбрано: укажите решение в строках, которые хотите изменить.'); return; }
  const bad = items.filter(i => (i.decision === 'page' && !i.slug) || (i.decision === 'refusal' && !i.reason));
  if (bad.length) { alert('Не хватает данных: ' + bad.map(i => i.term).join(', ')); return; }
  const data = { contract_version: '1.0', kind: 'page-candidates', items: items, failures: [] };
  const blob = new Blob([JSON.stringify(data, null, 1)], {type: 'application/json'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(blob);
  a.download = 'candidates-decisions.json';
  a.click();
}
document.getElementById('q').addEventListener('input', e => {
  const q = e.target.value.trim().toLowerCase();
  rows().forEach(r => r.classList.toggle('hidden',
    !!q && !(r.dataset.key + ' ' + r.dataset.name).toLowerCase().includes(q)));
});
document.getElementById('allRef').addEventListener('click', () => {
  rows().forEach(r => { if (!r.classList.contains('hidden')) { r.querySelector('.dec-sel').value = 'refusal'; } }); upd(); });
document.getElementById('resetMarks').addEventListener('click', () => {
  try { localStorage.removeItem(KEY()); } catch (e) {}
  rows().forEach(r => { r.querySelector('.dec-sel').value = ''; }); upd(); });
document.getElementById('export').addEventListener('click', exportJson);
rows().forEach(r => { r.querySelector('.dec-sel').addEventListener('change', upd);
  
  r.querySelector('.reason').addEventListener('change', save); });
load(); upd();
</script>"""
    return f"""<!doctype html>
<html lang="ru"><head><meta charset="utf-8"><title>Кандидаты в страницы: слежение за ростом упоминаний</title>
<style>
:root {{ color-scheme:dark; --bg:#0f131b; --card:#161d29; --line:#232e40; --ink:#e8eef5; --muted:#8b98a8;
        --new:#7ee787; --up:#79b8ff; --warn:#e0a33a; --bad:#ff7b72; }}
* {{ box-sizing:border-box; }}
body {{ margin:0; padding:18px; background:var(--bg); color:var(--ink);
       font:14px/1.5 -apple-system,Segoe UI,Roboto,Arial,sans-serif; }}
h1 {{ font-size:18px; margin:0 0 6px; }}
.sub {{ color:var(--muted); font-size:13px; margin-bottom:12px; }}
.kpis {{ display:flex; gap:10px; flex-wrap:wrap; margin-bottom:14px; }}
.kpi {{ background:var(--card); border:1px solid var(--line); border-left:3px solid var(--line);
       border-radius:10px; padding:8px 12px; }}
.kpi.warn {{ border-left-color:var(--warn); }}
.kpi.ok {{ border-left-color:var(--new); }}
.k {{ color:var(--muted); font-size:12px; text-transform:uppercase; }}
.v {{ font-size:17px; font-weight:600; }}
table {{ width:100%; border-collapse:collapse; }}
th {{ text-align:left; color:var(--muted); font-size:12px; text-transform:uppercase; }}
th, td {{ border-bottom:1px solid var(--line); padding:7px 9px; vertical-align:top; }}
tr.revisit {{ background:#241f12; }}
td.num {{ white-space:nowrap; }}
.was {{ color:var(--muted); font-size:12px; }}
.up {{ color:var(--new); font-size:12px; }}
.down {{ color:var(--bad); font-size:12px; }}
.key, .reason {{ color:var(--muted); font-size:12px; }}
.dec {{ font-size:12px; padding:1px 7px; border-radius:999px; border:1px solid var(--line); }}
.dec.page {{ color:var(--new); border-color:color-mix(in srgb, var(--new) 45%, transparent); }}
.dec.cover {{ color:var(--up); border-color:color-mix(in srgb, var(--up) 45%, transparent); }}
.dec.ref {{ color:var(--muted); }}
.syn {{ font-size:12px; color:var(--up); }}
tr.suspect {{ background:#221c10; }}
.bar {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; margin:12px 0; }}
.bar input[type=text] {{ background:#12161d; border:1px solid var(--line); color:var(--ink);
                        border-radius:8px; padding:5px 9px; }}
.bar button {{ background:var(--card); border:1px solid var(--line); color:var(--ink); border-radius:8px;
              padding:5px 11px; cursor:pointer; }}
.bar button.main {{ background:#24406b; border-color:#3b5f96; }}
#cnt {{ color:var(--muted); font-size:12px; margin-left:auto; }}
td.mark {{ white-space:nowrap; }}
td.mark select, td.mark input {{ background:#12161d; border:1px solid var(--line); color:var(--ink);
                                 border-radius:6px; padding:3px 6px; font-size:12px; max-width:190px; }}
td.mark input {{ width:150px; }}
tr.hidden {{ display:none; }}

.prose {{ display:inline-block; margin-top:2px; font-size:11px; padding:1px 5px; border-radius:6px;
         background:#334155; color:#cbd5e1; }}
</style></head><body>
<h1>Кандидаты в страницы: слежение за ростом упоминаний</h1>
<div class="sub">{stamp}<br>
Строки, подсвеченные жёлтым, — кандидаты, набравшие упоминания <b>после</b> решения владельца: их решение
стоит пересмотреть. Порог — вопрос, а не приказ: различает человек, машина только считает и не даёт забыть.</div>
<div class="kpis">
  <div class="kpi {'warn' if revisit else 'ok'}"><div class="k">Набрали упоминания после решения</div>
    <div class="v">{len(revisit)}</div></div>
  <div class="kpi"><div class="k">Всего кандидатов и решений</div><div class="v">{len(rows)}</div></div>
  <div class="kpi"><div class="k">Из них с решением владельца</div><div class="v">{decided}</div></div>
  <div class="kpi {'ok' if not undecided else 'warn'}"><div class="k">Без решения</div><div class="v">{len(undecided)}</div></div>
  <div class="kpi {'warn' if suspect else 'ok'}"><div class="k">Причину стоит уточнить (есть синоним)</div>
    <div class="v">{len(suspect)}</div></div>
</div>
{table_bar}
{table}
{script}
</body></html>
"""


def main():
    ap = argparse.ArgumentParser(description="Кандидаты в страницы: рост упоминаний после решения.")
    ap.add_argument("--wiki", default=".")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--baseline", action="store_true",
                    help="снять снимок упоминаний (делается после решений владельца)")
    a = ap.parse_args()
    root = os.path.abspath(a.wiki)
    vault = toolkit.wiki(root)
    rows, taken, _ = build_rows(root, vault)
    if a.baseline:
        keys = [r["key"] for r in rows]
        now = counts_for(vault, keys)
        data = {"taken": "2026-09-15", "note": "снимок упоминаний на момент решений владельца по кандидатам",
                "counts": {r["key"]: {"name": r["name"], "sources": now[r["key"]]["sources"],
                                      "pages": now[r["key"]]["pages"], "decision": r["decision"],
                                      "date": r["date"]} for r in rows}}
        path = toolkit.area(root, "audit", "candidates-baseline-2026-09-15.json")
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(json.dumps(data, ensure_ascii=False, indent=1))
        print(f"снимок упоминаний: {len(rows)} кандидатов → {os.path.relpath(path, root)}")
        return 0
    revisit = [r for r in rows if r["revisit"]]
    suspect = [r for r in rows if r["reason_suspect"]]
    print(f"кандидатов и решений: {len(rows)} · набрали упоминания после решения: {len(revisit)}")
    for r in revisit:
        print(f"  ! {r['name']} ({r['key']}): источников {r['src_was']}→{r['sources']}, "
              f"страниц {r['pages_was']}→{r['pages']}, решение «{r['decision']}»")
    if not a.write:
        print("(без --write лист не записан)")
        return 0
    with io.open(toolkit.area(root, OUT), "w", encoding="utf-8", newline="") as f:
        f.write(render(root, vault))
    print("записано:", OUT_DISPLAY)
    return 0


if __name__ == "__main__":
    sys.exit(main())
