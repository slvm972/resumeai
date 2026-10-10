#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time
import traceback
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests package missing")
    sys.exit(1)

def log(logfile, message):
    line = f"[{time.strftime('%H:%M:%S')}] {message}"
    print(line)
    if logfile:
        logfile.write(line + "\n")
        logfile.flush()

def login(session, base_url, username, password, logfile):
    log(logfile, f"Login as admin ({username})...")
    resp = session.post(
        f"{base_url}/api/login",
        json={"email": username, "password": password},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Login failed: HTTP {resp.status_code} — {resp.text[:300]}")
    body = resp.json()
    log(logfile, "Login successful.")
    token = body.get("access_token") or body.get("token") or body.get("accessToken")
    if token:
        session.headers.update({"Authorization": f"Bearer {token}"})
        log(logfile, "JWT token added to headers.")
    return body

def run_single_resume(session, base_url, docx_path, out_dir, logfile):
    result = {"file": docx_path.name, "status": "unknown", "steps": {}, "error": None, "timings_sec": {}}
    out_dir.mkdir(parents=True, exist_ok=True)
    original_copy = out_dir / "original.docx"
    original_copy.write_bytes(docx_path.read_bytes())

    try:
        log(logfile, f"[{docx_path.name}] Step 1/3: analyze...")
        t0 = time.time()
        with open(docx_path, "rb") as f:
            resp = session.post(
                f"{base_url}/api/analyze",
                files={"file": (docx_path.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                timeout=120,
            )
        result["timings_sec"]["analyze"] = round(time.time() - t0, 2)
        if resp.status_code != 200:
            result["status"] = "FAILED_ANALYZE"
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:500]}"
            log(logfile, f"[{docx_path.name}] analyze FAILED: {result['error']}")
            return result

        analyze_body = resp.json()
        (out_dir / "analyze_response.json").write_text(json.dumps(analyze_body, indent=2, ensure_ascii=False), encoding="utf-8")
        result["steps"]["analyze"] = "OK"

        log(logfile, f"[{docx_path.name}] Step 2/3: improve...")
        t0 = time.time()
        with open(docx_path, "rb") as f:
            resp = session.post(
                f"{base_url}/api/improve",
                files={"file": (docx_path.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                timeout=180,
            )
        result["timings_sec"]["improve"] = round(time.time() - t0, 2)
        if resp.status_code != 200:
            result["status"] = "FAILED_IMPROVE"
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:500]}"
            log(logfile, f"[{docx_path.name}] improve FAILED: {result['error']}")
            return result

        improve_body = resp.json()
        (out_dir / "improve_response.json").write_text(json.dumps(improve_body, indent=2, ensure_ascii=False), encoding="utf-8")
        result["steps"]["improve"] = "OK"
        result["tokens_used"] = improve_body.get("tokens_used")
        result["detected_language"] = improve_body.get("detected_language")

        quality_report = improve_body.get("quality_report")
        if quality_report:
            (out_dir / "quality_report.json").write_text(json.dumps(quality_report, indent=2, ensure_ascii=False), encoding="utf-8")
            result["quality_summary"] = quality_report.get("summary")

        log(logfile, f"[{docx_path.name}] Step 3/3: improve/docx...")
        t0 = time.time()
        improved_resume_text = improve_body.get("improved_resume", "")
        item_ids = improve_body.get("item_ids")
        with open(docx_path, "rb") as f:
            form_data = {"improved_resume": improved_resume_text}
            if item_ids is not None:
                form_data["item_ids"] = json.dumps(item_ids)
            resp = session.post(
                f"{base_url}/api/improve/docx",
                files={"original_file": (docx_path.name, f, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
                data=form_data,
                timeout=60,
            )
        result["timings_sec"]["improve_docx"] = round(time.time() - t0, 2)
        if resp.status_code != 200:
            result["status"] = "FAILED_IMPROVE_DOCX"
            result["error"] = f"HTTP {resp.status_code}: {resp.text[:500]}"
            log(logfile, f"[{docx_path.name}] improve/docx FAILED: {result['error']}")
            return result

        improved_docx_path = out_dir / "improved.docx"
        improved_docx_path.write_bytes(resp.content)
        result["steps"]["improve_docx"] = "OK"
        result["improved_docx_size"] = len(resp.content)
        result["original_docx_size"] = docx_path.stat().st_size
        result["status"] = "SUCCESS"
    except Exception as e:
        result["status"] = "EXCEPTION"
        result["error"] = f"{type(e).__name__}: {e}"
        log(logfile, f"[{docx_path.name}] EXCEPTION: {result['error']}")
        log(logfile, traceback.format_exc())
    return result

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:5000")
    parser.add_argument("--admin-user", default=None)
    parser.add_argument("--admin-pass", default=None)
    args = parser.parse_args()

    args.admin_user = args.admin_user or os.environ.get("ADMIN_EMAIL")
    args.admin_pass = args.admin_pass or os.environ.get("ADMIN_PASSWORD")

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    docx_files = sorted(input_dir.glob("*.docx"))

    main_logfile = open(output_dir / "batch_log.txt", "w", encoding="utf-8")
    session = requests.Session()

    try:
        login(session, args.base_url, args.admin_user, args.admin_pass, main_logfile)
    except Exception as e:
        log(main_logfile, f"CRITICAL ERROR: {e}")
        main_logfile.close()
        sys.exit(1)

    results = []
    t_start = time.time()
    for docx_path in docx_files:
        file_out_dir = output_dir / docx_path.stem
        file_log_path = file_out_dir / "log.txt"
        file_out_dir.mkdir(parents=True, exist_ok=True)
        with open(file_log_path, "w", encoding="utf-8") as fl:
            log(main_logfile, f"--- Processing {docx_path.name} ---")
            res = run_single_resume(session, args.base_url, docx_path, file_out_dir, fl)
            results.append(res)
            log(main_logfile, f"--- {docx_path.name}: {res['status']} ---")
        
        # Пауза 3 секунды между запросами для обхода лимита токенов
        time.sleep(3)

    summary = {
        "total_files": len(docx_files),
        "success": sum(1 for r in results if r["status"] == "SUCCESS"),
        "failed": sum(1 for r in results if r["status"] != "SUCCESS"),
        "total_time_sec": round(time.time() - t_start, 2),
        "results": results,
    }
    (output_dir / "batch_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    main_logfile.close()
    print("Done. Success:", summary["success"], "Failed:", summary["failed"])

if __name__ == "__main__":
    main()