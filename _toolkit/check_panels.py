#!/usr/bin/env python3
"""Проверка отрисованных панелей: что видит владелец, открывая дашборд.

Утверждения, дословно:
1. Панели на месте: каждая требуемая панель существует и непуста.
2. Порядок панель → агрегат: встроенная в дашборд копия каждой панели равна
   текущему файлу панели побайтово — агрегат, собранный раньше панелей, виден
   как несовпадение, а не как свежесть.
3. Встроенное содержимое непустое: каждый `srcdoc` после разэкранирования непуст.
4. `srcdoc` читается: содержимое разбирается `html.parser` без исключений.
Инлайн-скрипт дашборда гоняется через `node --check`. Гарантия действует при
node на машине, иначе проверка скрипта записана неприменимой вслух — правило
не обещает больше, чем даёт. Хвостовой `--wiki` от цепочки терпим.
Отчёт — парой, не вердиктом.
"""
import argparse
import html
import html.parser
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import panel_freshness
import toolkit

AGGR = "_staging/dashboard.html"

IFRAME_RE = re.compile(r"<iframe\b[^>]*srcdoc=\"([^\"]*)\"", re.S)
SCRIPT_RE = re.compile(r"<script>(.*?)</script>", re.S)


class _Sink(html.parser.HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = 0

    def handle_starttag(self, tag, attrs):
        self.tags += 1


def read_text(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def check(root):
    problems = []
    dash = toolkit.area(root, "dashboard.html")
    if not os.path.isfile(dash) or os.path.getsize(dash) == 0:
        return ["дашборд не собран или пуст: _staging/dashboard.html"], False
    panels = {}
    stage = toolkit.area(root)
    for base, _dirs, files in os.walk(stage):
        for fn in files:
            if fn.endswith(".html"):
                p = os.path.join(base, fn)
                panels[os.path.relpath(p, root).replace(os.sep, "/")] = read_text(p)
    required = panel_freshness.required_panels(root)
    missing = [r for r in required
               if not os.path.isfile(os.path.join(root, *r.split("/")))
               or os.path.getsize(os.path.join(root, *r.split("/"))) == 0]
    if missing:
        problems.append("панели не собраны: " + ", ".join(missing))
    doc = read_text(dash)
    srcdocs = [html.unescape(m) for m in IFRAME_RE.findall(doc)]
    if not srcdocs:
        problems.append("в дашборде нет ни одного srcdoc")
    for i, content in enumerate(srcdocs):
        if not content.strip():
            problems.append(f"srcdoc #{i} пуст")
            continue
        sink = _Sink()
        try:
            sink.feed(content)
        except Exception as e:  # noqa: BLE001 — парсер обязан пережить любой мусор вслух
            problems.append(f"srcdoc #{i} не разбирается: {e}")
            continue
        if sink.tags == 0:
            problems.append(f"srcdoc #{i}: ни одного тега")
    matched = set()
    for i, content in enumerate(srcdocs):
        if not content.strip():
            continue
        for rel, body in panels.items():
            if rel != AGGR and content == body:
                matched.add(rel)
                break
        else:
            problems.append(f"srcdoc #{i} не совпал ни с одной панелью — агрегат старше панелей")
    for rel in required:
        if rel != AGGR and rel in panels and rel not in matched:
            problems.append(f"панель не встроена в агрегат: {rel}")
    scripts = [s for s in SCRIPT_RE.findall(doc) if s.strip()]
    node = shutil.which("node")
    if scripts and node:
        for i, body in enumerate(scripts):
            js = os.path.join(tempfile.mkdtemp(prefix="panel-js-"), "inline.js")
            with open(js, "w", encoding="utf-8") as f:
                f.write(body)
            r = subprocess.run([node, "--check", js], capture_output=True, text=True,
                               errors="replace", check=False)
            if r.returncode != 0:
                problems.append(f"инлайн-скрипт #{i} не проходит node --check")
    lines = []
    if not problems:
        lines.append(f"панелей встроено: {len(matched)}, srcdoc читаются, порядок панель→агрегат держится")
    if scripts:
        if node:
            lines.append("инлайн-скрипт: node --check пройден" if not problems else "инлайн-скрипт проверен")
        else:
            lines.append("проверка скрипта неприменима: node нет на машине")
    return problems if problems else lines, not problems


def self_test():
    from dashboard import embedded
    import panel_freshness as pf
    real_pairs, real_req = pf.PAIRS, pf.required_panels
    tmp = tempfile.mkdtemp(prefix="panels-")
    os.makedirs(toolkit.area(tmp), exist_ok=True)
    panel = toolkit.area(tmp, "p.html")
    open(panel, "w", encoding="utf-8").write("<html><body>Панель</body></html>")
    dash = toolkit.area(tmp, "dashboard.html")
    open(dash, "w", encoding="utf-8").write(
        "<html><body>" + embedded(panel, "Тест") + "<script>var a = 1;</script></body></html>")
    pf.PAIRS = [("_staging/p.html", [])]
    pf.required_panels = lambda root: ["_staging/p.html"]
    problems, ok = check(tmp)
    pf.PAIRS, pf.required_panels = real_pairs, real_req
    print(f"чистая панель: {'прошла' if ok else 'УПАЛА: ' + '; '.join(problems)}")
    if not ok:
        return 1
    doc = read_text(dash)
    foreign = html.escape("<html><body>Чужая панель</body></html>")
    open(dash, "w", encoding="utf-8").write(IFRAME_RE.sub(
        lambda m: '<iframe srcdoc="' + foreign + '"', doc, count=1))
    pf.PAIRS = [("_staging/p.html", [])]
    pf.required_panels = lambda root: ["_staging/p.html"]
    problems, ok = check(tmp)
    pf.PAIRS, pf.required_panels = real_pairs, real_req
    print(f"порча srcdoc: {'поймана' if not ok else 'ПРОПУЩЕНА'}")
    if ok:
        return 1
    print("оба знака сошлись")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Проверка отрисованных панелей дашборда.")
    ap.add_argument("--wiki", default=".", help="корень проекта")
    ap.add_argument("--self-test", action="store_true", help="доказательство в обе стороны на песочнице")
    a = ap.parse_args(argv)
    if a.self_test:
        return self_test()
    root = os.path.abspath(a.wiki)
    problems, ok = check(root)
    print("\n".join(problems))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
