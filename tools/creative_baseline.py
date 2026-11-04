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

from app.rewrite_creative import _line_key, split_sections, rewrite_with_repair  # noqa: E402
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


def summarize_run(source, rr):
    """Метрики одного прогона из результата rewrite_with_repair."""
    issues = list(rr.get("final_issues") or [])
    by_type = {}
    for it in issues:
        by_type[it.get("type", "?")] = by_type.get(it.get("type", "?"), 0) + 1
    main_text = split_sections(rr.get("text") or "")[0]
    return {
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
    }


def error_run(message):
    return {"status": "error", "rounds": None, "changed_ratio": None, "unresolved": None, "calls": None,
            "judge_error": False, "budget_exhausted": False, "issue_counts": {}, "issues": [],
            "lost_number": [], "embellishment": [], "final_text": None, "error": message}


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
        }
    return rows, totals


def _fmt(v, pct=False):
    if v is None:
        return "-"
    return ("%.0f%%" % (v * 100)) if pct else ("%.2f" % v)


def format_table(rows, totals):
    head = "%-34s %-9s %4s %4s %8s %10s %6s %8s %10s" % (
        "resume", "model", "runs", "err", "changed", "unresolved", "calls", "LOST_NUM", "EMBELLISH")
    lines = [head, "-" * len(head)]
    for (resume, label) in sorted(rows):
        r = rows[(resume, label)]
        lines.append("%-34s %-9s %4d %4d %8s %10s %6s %8s %10s" % (
            resume[:34], label, r["runs"], r["errors"], _fmt(r["changed_ratio"], True), _fmt(r["unresolved"]),
            _fmt(r["calls"]), _fmt(r["lost_number"]), _fmt(r["embellishment"])))
    lines.append("-" * len(head))
    for label in sorted(totals):
        r = totals[label]
        lines.append("%-34s %-9s %4d %4d %8s %10s %6s %8s %10s" % (
            "AVERAGE", label, r["runs"], r["errors"], _fmt(r["changed_ratio"], True), _fmt(r["unresolved"]),
            _fmt(r["calls"]), _fmt(r["lost_number"]), _fmt(r["embellishment"])))
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


def main(argv=None, rewrite_fn=None):
    ap = argparse.ArgumentParser(description="Creative baseline (C1c-0)")
    ap.add_argument("--input", required=True, help="папка с weak3_*.json (или *.txt)")
    ap.add_argument("--output", required=True, help="папка для результатов")
    ap.add_argument("--runs", type=int, default=2, help="прогонов на каждую пару резюме x модель")
    ap.add_argument("--pause", type=float, default=0.0, help="пауза между запросами к API, секунды")
    args = ap.parse_args(argv)

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
