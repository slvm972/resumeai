# tools/creative_baseline.py
"""
Cycle C1c-0: базовая линия Creative-режима (код продукта не меняется).

Для каждого резюме x 2 модели (основная GROQ_MODEL_EDITOR и резервная GROQ_MODEL_FALLBACK,
как в app/services/groq_params.py) x N прогонов вызывает app.rewrite_creative.rewrite_with_repair
НАПРЯМУЮ, без сервера. Судья всегда GROQ_MODEL_JUDGE.

Ключ берётся ТОЛЬКО из переменной окружения GROQ_API_KEY и нигде не печатается и не пишется.

Вход: папка с файлами weak3_*.json (старые результаты, из них берутся ИСХОДНЫЕ тексты резюме:
quality_report.blocks[].original_text в порядке item_ids) и/или обычные *.txt.

На каждый прогон пишется <output>/<резюме>__<основная|резервная>__runK.json:
  changed_ratio   доля строк исходника, которых нет в результате дословно (после снятия маркеров списка)
  unresolved      число замечаний, оставшихся после ремонта (final_issues)
  calls           число запросов к LLM (переписывание + судья + ремонт)
  issues          список замечаний {type, quote, reason}; lost_number / embellishment выделены отдельно
  final_text      итоговый текст (после assemble_final)
При ошибке API или лимите скрипт не падает: ошибка пишется в JSON, идём дальше.

Cycle D2 - честная метрика (старые поля остаются, добавлены новые):
  substantive_changed_ratio  доля ИЗМЕНЁННЫХ строк среди СОДЕРЖАТЕЛЬНЫХ строк исходника
  substantive_total / substantive_changed / substantive_lines  знаменатель, числитель и сами строки
  added_lines                сколько содержательных строк основного текста результата отсутствует в
                             исходнике (дословно, после снятия маркера списка) и не является заголовком.
                             Сюда входят и переписанные строки, и добавленное резюме. Чистые добавления
                             приблизительно = added_lines - substantive_changed.

Содержательная строка исходника (детерминированное правило, classify_lines / substantive_lines):
  НЕ содержательны: (1) первая непустая строка (имя); (2) строка с контактами (e-mail, URL, телефон);
  (3) заголовок раздела (Experience/Опыт работы/Education/Skills/... из списков rewrite_creative
  плюс несколько украинских/русских вариантов, либо короткая строка ЗАГЛАВНЫМИ); (4) строка "должность,
  компания, годы": есть диапазон лет (2019 - 2024, 2018-present), есть запятая, нет маркера списка;
  (5) любая строка внутри раздела образования (от заголовка Education/Образование/Освіта до следующего заголовка).
  Всё остальное содержательно: буллеты опыта и строка навыков (её можно перегруппировать).
  Правило подобрано по W1-W6: во всех шести резюме оно оставляет ровно 5 строк (4 буллета + навыки).

Пересчёт без API и без ключа:
  python tools/creative_baseline.py --recompute .\\baseline_prod --sources .\\weak3 [--output .\\recomputed]
  (берутся сохранённые *.json прогонов; метрики считаются заново по final_text и исходнику)

Запуск (PowerShell, из корня репозитория):
  $env:GROQ_API_KEY = "<ключ>"
  python tools/creative_baseline.py --input .\\weak3 --output .\\baseline_out --runs 2
"""
import argparse
import glob
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Сервер здесь не запускается, но config.py при импорте проверяет окружение.
# Заглушки ставятся ТОЛЬКО если переменных нет; реальные значения не перезаписываются.
os.environ.setdefault("FLASK_ENV", "testing")
os.environ.setdefault("SECRET_KEY", "baseline-tool-not-used")
os.environ.setdefault("JWT_SECRET_KEY", "baseline-tool-not-used")

import re  # noqa: E402

from app.rewrite_creative import (  # noqa: E402
    _line_key, split_sections, rewrite_with_repair, extract_contacts, _norm_heading, _is_heading_line,
    _EXPERIENCE_ALIASES, _OTHER_SECTION_ALIASES, _SKILLS_HEADINGS,
)
from app.services.groq_params import groq_model  # noqa: E402

MODEL_LABELS = ("main", "fallback")     # основная / резервная


# ---------------------------------------------------------------------------
# Чистые функции (тестируются без сети)
# ---------------------------------------------------------------------------

def load_source(path):
    """Исходный текст резюме из weak3_*.json (original_text блоков в порядке item_ids) или из .txt."""
    if path.lower().endswith(".txt"):
        with open(path, encoding="utf-8") as f:
            return f.read().strip()
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    part = data.get("precise") or data.get("creative") or data
    ids = part["item_ids"]
    originals = {}
    for b in part["quality_report"]["blocks"]:
        if b.get("original_text") is not None:
            originals.setdefault(b["id"], b["original_text"])   # повторы retry пропускаем
    lines = [originals[i] for i in ids if i in originals]
    if not lines:
        raise ValueError("no original_text found in %s" % os.path.basename(path))
    return "\n".join(lines)


def changed_ratio(source, output_main):
    """Доля непустых строк исходника, которых нет в результате дословно (без маркеров списка и лишних пробелов)."""
    src_keys = [_line_key(l) for l in (source or "").splitlines() if l.strip()]
    if not src_keys:
        return 0.0
    out_keys = {_line_key(l) for l in (output_main or "").splitlines() if l.strip()}
    changed = sum(1 for k in src_keys if k not in out_keys)
    return round(changed / len(src_keys), 3)


# ---- Cycle D2: содержательные строки ----
_EDU_HEADINGS = {"education", "образование", "освіта", "השכלה"}
_EXTRA_HEADINGS = {"профіль", "про себе", "резюме", "обо мне", "навички", "освіта"}
_ALL_HEADINGS = set(_EXPERIENCE_ALIASES) | set(_OTHER_SECTION_ALIASES) | set(_SKILLS_HEADINGS) | _EXTRA_HEADINGS
_YEAR_RANGE_RE = re.compile(r"(?<!\d)(?:19|20)\d{2}\s*[-\u2013\u2014]\s*(?:(?:19|20)\d{2}(?!\d)|[^\W\d_]{3,})")
_MARKER_RE = re.compile(r"^\s*(?:[-\u2022*\u2013\u2014]|\d+[.)])\s+")


def is_heading(line):
    h = _norm_heading(line)
    return bool(h) and (h in _ALL_HEADINGS or _is_heading_line(line.strip()))


def _is_contact_line(line):
    return bool(extract_contacts(line))


def _is_job_line(line):
    """Строка "должность, компания, годы": диапазон лет + запятая, без маркера списка."""
    return bool(_YEAR_RANGE_RE.search(line)) and "," in line and not _MARKER_RE.match(line)


def classify_lines(text):
    """[(строка, содержательна: bool)] для всех непустых строк текста (правило описано в docstring модуля)."""
    out, section, first = [], None, True
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        if first:
            first = False
            out.append((line, False))                      # (1) имя
            continue
        if _is_contact_line(line):                          # (2) контакты
            out.append((line, False))
            continue
        if is_heading(line):                                # (3) заголовок
            section = "edu" if _norm_heading(line) in _EDU_HEADINGS else "other"
            out.append((line, False))
            continue
        if _is_job_line(line) or section == "edu":          # (4) должность/компания/годы, (5) образование
            out.append((line, False))
            continue
        out.append((line, True))
    return out


def substantive_lines(text):
    return [l for l, ok in classify_lines(text) if ok]


def substantive_stats(source, output_main):
    """(знаменатель, изменено, доля) по содержательным строкам исходника; изменено = нет дословно в результате."""
    src = substantive_lines(source)
    out_keys = {_line_key(l) for l in (output_main or "").splitlines() if l.strip()}
    changed = sum(1 for l in src if _line_key(l) not in out_keys)
    ratio = round(changed / len(src), 3) if src else 0.0
    return len(src), changed, ratio


def added_lines_list(source, output_main):
    """Содержательные строки результата, которых нет в исходнике (после снятия маркера списка)."""
    src_keys = {_line_key(l) for l in (source or "").splitlines() if l.strip()}
    return [l for l in substantive_lines(output_main) if _line_key(l) not in src_keys]


def summarize_run(source, rr):
    """Метрики одного прогона из результата rewrite_with_repair."""
    issues = list(rr.get("final_issues") or [])
    by_type = {}
    for it in issues:
        by_type[it.get("type", "?")] = by_type.get(it.get("type", "?"), 0) + 1
    main_text = split_sections(rr.get("text") or "")[0]
    return {**_d2_metrics(source, main_text), **{
        "status": rr.get("status"),
        "rounds": rr.get("rounds"),
        "changed_ratio": changed_ratio(source, main_text),
        "unresolved": len(issues),
        "calls": rr.get("calls"),
        "judge_error": bool(rr.get("judge_error")),
        "budget_exhausted": bool(rr.get("budget_exhausted")),
        "issue_counts": by_type,
        "issues": issues,
        "lost_number": [i for i in issues if i.get("type") == "LOST_NUMBER"],
        "embellishment": [i for i in issues if i.get("type") == "EMBELLISHMENT"],   # "домыслы"
        "final_text": rr.get("final_text"),
        "error": None,
    }}


def _d2_metrics(source, main_text):
    total, changed, ratio = substantive_stats(source, main_text)
    added = added_lines_list(source, main_text)
    return {
        "substantive_total": total,
        "substantive_changed": changed,
        "substantive_changed_ratio": ratio,
        "substantive_lines": substantive_lines(source),
        "added_lines": len(added),
        "added_lines_text": added,
    }


def error_run(message):
    return {"status": "error", "rounds": None, "changed_ratio": None, "unresolved": None, "calls": None,
            "judge_error": False, "budget_exhausted": False, "issue_counts": {}, "issues": [],
            "lost_number": [], "embellishment": [], "final_text": None, "error": message,
            "substantive_total": None, "substantive_changed": None, "substantive_changed_ratio": None,
            "substantive_lines": [], "added_lines": None, "added_lines_text": []}


def _mean(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def aggregate(runs):
    """runs: список dict {resume, label, ...метрики}. Возвращает {(resume,label): средние} и общие средние по label."""
    per = {}
    for r in runs:
        per.setdefault((r["resume"], r["label"]), []).append(r)
    rows = {}
    for key, lst in per.items():
        ok = [x for x in lst if not x.get("error")]
        rows[key] = {
            "runs": len(lst), "errors": len(lst) - len(ok),
            "changed_ratio": _mean(x["changed_ratio"] for x in ok),
            "unresolved": _mean(x["unresolved"] for x in ok),
            "calls": _mean(x["calls"] for x in ok),
            "lost_number": _mean(len(x["lost_number"]) for x in ok),
            "embellishment": _mean(len(x["embellishment"]) for x in ok),
            "substantive_changed_ratio": _mean(x.get("substantive_changed_ratio") for x in ok),
            "added_lines": _mean(x.get("added_lines") for x in ok),
        }
    totals = {}
    for label in {k[1] for k in rows}:
        ok = [r for r in runs if r["label"] == label and not r.get("error")]
        totals[label] = {
            "runs": sum(1 for r in runs if r["label"] == label), "errors": sum(1 for r in runs if r["label"] == label and r.get("error")),
            "changed_ratio": _mean(x["changed_ratio"] for x in ok),
            "unresolved": _mean(x["unresolved"] for x in ok),
            "calls": _mean(x["calls"] for x in ok),
            "lost_number": _mean(len(x["lost_number"]) for x in ok),
            "embellishment": _mean(len(x["embellishment"]) for x in ok),
            "substantive_changed_ratio": _mean(x.get("substantive_changed_ratio") for x in ok),
            "added_lines": _mean(x.get("added_lines") for x in ok),
        }
    return rows, totals


def _fmt(v, pct=False):
    if v is None:
        return "-"
    return ("%.0f%%" % (v * 100)) if pct else ("%.2f" % v)


def format_table(rows, totals):
    head = "%-34s %-9s %4s %4s %8s %10s %6s %8s %10s %9s %6s" % (
        "resume", "model", "runs", "err", "changed", "unresolved", "calls", "LOST_NUM", "EMBELLISH",
        "SUBST_CHG", "ADDED")
    lines = [head, "-" * len(head)]
    for (resume, label) in sorted(rows):
        r = rows[(resume, label)]
        lines.append("%-34s %-9s %4d %4d %8s %10s %6s %8s %10s %9s %6s" % (
            resume[:34], label, r["runs"], r["errors"], _fmt(r["changed_ratio"], True), _fmt(r["unresolved"]),
            _fmt(r["calls"]), _fmt(r["lost_number"]), _fmt(r["embellishment"]),
            _fmt(r.get("substantive_changed_ratio"), True), _fmt(r.get("added_lines"))))
    lines.append("-" * len(head))
    for label in sorted(totals):
        r = totals[label]
        lines.append("%-34s %-9s %4d %4d %8s %10s %6s %8s %10s %9s %6s" % (
            "AVERAGE", label, r["runs"], r["errors"], _fmt(r["changed_ratio"], True), _fmt(r["unresolved"]),
            _fmt(r["calls"]), _fmt(r["lost_number"]), _fmt(r["embellishment"]),
            _fmt(r.get("substantive_changed_ratio"), True), _fmt(r.get("added_lines"))))
    return "\n".join(lines)


def _redact(text, key):
    text = str(text)
    return text.replace(key, "***") if key else text


# ---------------------------------------------------------------------------
# Запуск
# ---------------------------------------------------------------------------

def collect_inputs(folder):
    paths = sorted(glob.glob(os.path.join(folder, "*.json")) + glob.glob(os.path.join(folder, "*.txt")))
    return [(os.path.splitext(os.path.basename(p))[0], p) for p in paths]


def recompute_runs(run_dir, sources_dir):
    """Пересчитать метрики по сохранённым *.json прогонов (без сети и ключа). Возвращает (runs, notes)."""
    runs, notes = [], []
    for path in sorted(glob.glob(os.path.join(run_dir, "*.json"))):
        if os.path.basename(path).startswith("summary"):
            continue
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
        if not isinstance(rec, dict) or "resume" not in rec or "label" not in rec:
            continue
        if rec.get("error"):
            runs.append(rec)
            continue
        src_path = None
        for ext in (".json", ".txt"):
            cand = os.path.join(sources_dir, rec["resume"] + ext)
            if os.path.exists(cand):
                src_path = cand
                break
        if src_path is None:
            notes.append("no source for %s (skipped)" % rec["resume"])
            continue
        source = load_source(src_path)
        main_text = split_sections(rec.get("final_text") or "")[0]
        new_changed = changed_ratio(source, main_text)
        if rec.get("changed_ratio") is not None and abs(new_changed - rec["changed_ratio"]) > 0.001:
            notes.append("%s %s run%s: changed_ratio saved=%s recomputed=%s" % (
                rec["resume"], rec["label"], rec.get("run"), rec["changed_ratio"], new_changed))
        rec = {**rec, "changed_ratio": new_changed, **_d2_metrics(source, main_text)}
        runs.append(rec)
    return runs, notes


def main(argv=None, rewrite_fn=None):
    ap = argparse.ArgumentParser(description="Creative baseline (C1c-0)")
    ap.add_argument("--input", help="папка с weak3_*.json (или *.txt)")
    ap.add_argument("--output", help="папка для результатов (в режиме --recompute необязательна)")
    ap.add_argument("--recompute", help="папка с json прогона: пересчёт метрик без API и ключа")
    ap.add_argument("--sources", help="папка с weak3_*.json (исходники) для --recompute")
    ap.add_argument("--runs", type=int, default=2, help="прогонов на каждую пару резюме x модель")
    ap.add_argument("--pause", type=float, default=0.0, help="пауза между запросами к API, секунды")
    args = ap.parse_args(argv)

    if args.recompute:
        if not args.sources:
            ap.error("--recompute requires --sources")
        runs, notes = recompute_runs(args.recompute, args.sources)
        if not runs:
            print("No run *.json files in %s" % args.recompute)
            return 2
        rows, totals = aggregate(runs)
        table = format_table(rows, totals)
        if args.output:
            os.makedirs(args.output, exist_ok=True)
            with open(os.path.join(args.output, "summary_recomputed.txt"), "w", encoding="utf-8") as f:
                f.write(table + "\n")
            with open(os.path.join(args.output, "summary_recomputed.json"), "w", encoding="utf-8") as f:
                json.dump({"rows": {"%s|%s" % k: v for k, v in rows.items()}, "totals": totals}, f,
                          ensure_ascii=False, indent=2)
        for n in notes:
            print("NOTE:", n)
        print(table)
        return 0

    if not args.input or not args.output:
        ap.error("--input and --output are required (or use --recompute)")

    api_key = os.environ.get("GROQ_API_KEY", "").strip()
    if not api_key:
        print("GROQ_API_KEY is not set in the environment.")
        return 2

    inputs = collect_inputs(args.input)
    if not inputs:
        print("No *.json / *.txt files in %s" % args.input)
        return 2
    os.makedirs(args.output, exist_ok=True)

    rewrite_fn = rewrite_fn or rewrite_with_repair
    models = {"main": groq_model("GROQ_MODEL_EDITOR"), "fallback": groq_model("GROQ_MODEL_FALLBACK")}
    judge_model = groq_model("GROQ_MODEL_JUDGE")
    print("models: main=%s fallback=%s judge=%s" % (models["main"], models["fallback"], judge_model))

    all_runs = []
    for name, path in inputs:
        try:
            source = load_source(path)
        except Exception as e:
            print("SKIP %s: %s" % (name, _redact(e, api_key)))
            continue
        for label in MODEL_LABELS:
            for k in range(1, args.runs + 1):
                try:
                    rr = rewrite_fn(source, models[label], judge_model, api_key=api_key, pause=args.pause)
                    metrics = summarize_run(source, rr)
                except Exception as e:
                    metrics = error_run("%s: %s" % (type(e).__name__, _redact(e, api_key)[:300]))
                record = {"resume": name, "label": label, "model": models[label], "judge_model": judge_model,
                          "run": k, **metrics}
                all_runs.append(record)
                with open(os.path.join(args.output, "%s__%s__run%d.json" % (name, label, k)), "w", encoding="utf-8") as f:
                    json.dump(record, f, ensure_ascii=False, indent=2)
                print("  %s %s run%d: %s" % (name[:30], label, k, record["error"] or
                      "changed=%s unresolved=%s calls=%s" % (_fmt(record["changed_ratio"], True), record["unresolved"], record["calls"])))

    rows, totals = aggregate(all_runs)
    table = format_table(rows, totals)
    with open(os.path.join(args.output, "summary.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": {"%s|%s" % k: v for k, v in rows.items()}, "totals": totals}, f, ensure_ascii=False, indent=2)
    with open(os.path.join(args.output, "summary.txt"), "w", encoding="utf-8") as f:
        f.write(table + "\n")
    print()
    print(table)
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    sys.exit(main())
