#!/usr/bin/env python3
"""Fleet Watch — deterministic morning verification loop.

Runs read-only checks against Supabase Postgres (pg_cron job status, data
freshness), HTTP health endpoints, and remote hosts over SSH, then sends a
single Telegram report. No LLM calls, no write operations, zero cost per run.

Usage:
    python3 fleet_watch.py --config config.json [--dry-run]

Exit codes: 0 = all OK, 1 = at least one FAIL, 2 = reporting error.
"""

import argparse
import json
import os
import shlex
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone

PSQL_TIMEOUT_S = 30
SSH_TIMEOUT_S = 30
HTTP_TIMEOUT_S = 15
TELEGRAM_CHUNK = 3800

OK, WARN, FAIL, SKIP = "OK", "WARN", "FAIL", "SKIP"
ICON = {OK: "✅", WARN: "\U0001f7e1", FAIL: "❌", SKIP: "⏭"}


@dataclass
class Result:
    name: str
    status: str
    detail: str


# ---------------------------------------------------------------- helpers

def run_cmd(argv, timeout, extra_env=None):
    """Run a command, return (returncode, stdout, stderr). Never raises."""
    env = dict(os.environ)
    if extra_env:
        env.update(extra_env)
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=env
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except subprocess.TimeoutExpired:
        return -1, "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return -1, "", f"binary not found: {exc.filename}"


def run_psql(db_url_env, sql):
    """Run a read-only query via psql. Returns (ok, rows_or_error).

    Rows are lists of '|'-separated fields. The connection string is read
    from the environment variable named by db_url_env (never logged).
    """
    conn = os.environ.get(db_url_env, "")
    if not conn:
        return False, f"env {db_url_env} not set"
    code, out, err = run_cmd(
        ["psql", conn, "-X", "-tA", "-F", "|", "-v", "ON_ERROR_STOP=1", "-c", sql],
        PSQL_TIMEOUT_S,
        extra_env={
            "PGCONNECT_TIMEOUT": "10",
            "PGOPTIONS": "-c statement_timeout=20000 -c default_transaction_read_only=on",
        },
    )
    if code != 0:
        # Strip anything that could echo the connection string.
        return False, (err or "psql failed").splitlines()[0][:200]
    return True, [line.split("|") for line in out.splitlines() if line.strip()]


def hours_ago(epoch_seconds):
    return (datetime.now(timezone.utc).timestamp() - epoch_seconds) / 3600.0


# ---------------------------------------------------------------- checks

def check_pg_cron(cfg):
    """Inspect cron.job: last run status + staleness for active jobs."""
    sql = (
        "SELECT j.jobname, j.active::text, coalesce(d.status,'never'), "
        "coalesce(floor(extract(epoch from now() - d.start_time))::text,'') "
        "FROM cron.job j LEFT JOIN LATERAL ("
        "  SELECT status, start_time FROM cron.job_run_details "
        "  WHERE jobid = j.jobid ORDER BY start_time DESC LIMIT 1"
        ") d ON true ORDER BY j.jobname;"
    )
    ok, rows = run_psql(cfg["db_url_env"], sql)
    if not ok:
        return Result(cfg["name"], FAIL, rows)

    stale_cfg = cfg.get("stale_after_hours", {})
    expect_active = set(cfg.get("expect_active", []))
    problems, infos = [], []
    seen = set()

    for jobname, active, last_status, age_s in rows:
        seen.add(jobname)
        # psql -tA renders booleans as t/f
        if active != "t":
            if jobname in expect_active:
                problems.append(f"{jobname}: INACTIVE but expected active")
            continue
        if last_status == "failed":
            problems.append(f"{jobname}: last run FAILED")
        elif last_status == "never":
            problems.append(f"{jobname}: active but never ran")
        elif last_status not in ("succeeded", "running"):
            problems.append(f"{jobname}: unexpected status '{last_status}'")
        limit = stale_cfg.get(jobname)
        if limit and age_s:
            age_h = float(age_s) / 3600.0
            if age_h > limit:
                problems.append(f"{jobname}: stale, last run {age_h:.0f}h ago (limit {limit}h)")

    for jobname in expect_active - seen:
        problems.append(f"{jobname}: job not found in cron.job")

    if problems:
        return Result(cfg["name"], FAIL, "; ".join(problems))
    infos.append(f"{len(rows)} jobs checked")
    return Result(cfg["name"], OK, "; ".join(infos))


def check_freshness(cfg):
    """Verify a timestamp expression is newer than max_age_hours.

    cfg.timestamp_sql must be a scalar SELECT returning one timestamptz,
    e.g. "SELECT max(created_at) FROM opportunities". It is trusted operator
    input (config.json must be chmod 600); a single statement is enforced.
    """
    ts_sql = cfg["timestamp_sql"].strip().rstrip(";")
    if ";" in ts_sql:
        return Result(cfg["name"], FAIL, "timestamp_sql must be a single SELECT (no semicolons)")
    sql = "SELECT floor(extract(epoch from now() - ((" + ts_sql + "))))::bigint;"
    ok, rows = run_psql(cfg["db_url_env"], sql)
    if not ok:
        return Result(cfg["name"], FAIL, rows)
    if not rows or not rows[0][0]:
        return Result(cfg["name"], FAIL, "no rows / NULL timestamp (table empty?)")
    age_h = float(rows[0][0]) / 3600.0
    limit = cfg["max_age_hours"]
    if age_h < -0.1:  # future timestamp = clock skew or bad data, never silently OK
        return Result(cfg["name"], WARN, f"timestamp {abs(age_h):.1f}h in the FUTURE")
    status = OK if age_h <= limit else FAIL
    return Result(cfg["name"], status, f"newest {age_h:.1f}h ago (limit {limit}h)")


def check_http(cfg):
    """GET a URL, expect a status code and optionally a body substring."""
    req = urllib.request.Request(cfg["url"], headers={"User-Agent": "fleet-watch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
            body = resp.read(65536).decode("utf-8", "replace")
            code = resp.status
    except urllib.error.HTTPError as exc:
        code = exc.code
        body = exc.read(65536).decode("utf-8", "replace") if exc.fp else ""
    except Exception as exc:
        return Result(cfg["name"], FAIL, f"request failed: {exc}")
    expect_code = cfg.get("expect_status", 200)
    if code != expect_code:
        return Result(cfg["name"], FAIL, f"status {code}, expected {expect_code}")
    needle = cfg.get("expect_substring")
    if needle and needle not in body:
        return Result(cfg["name"], FAIL, f"status {code} but body missing '{needle}'")
    return Result(cfg["name"], OK, f"status {code}")


def check_ssh_file_age(cfg):
    """Newest file under a remote path must be younger than max_age_hours."""
    # GNU find required on the remote (fine on Ubuntu targets)
    quoted_path = shlex.quote(cfg["path"])
    remote = (
        f"find {quoted_path} -type f -printf '%T@\\n' 2>/dev/null | sort -rn | head -1"
    )
    code, out, err = run_cmd(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", cfg["host"], remote],
        SSH_TIMEOUT_S,
    )
    if code != 0:
        return Result(cfg["name"], FAIL, f"ssh failed: {err or code}")
    if not out:
        return Result(cfg["name"], FAIL, f"no files under {cfg['path']}")
    age_h = hours_ago(float(out.splitlines()[0]))
    limit = cfg["max_age_hours"]
    status = OK if age_h <= limit else FAIL
    return Result(cfg["name"], status, f"newest file {age_h:.1f}h ago (limit {limit}h)")


def check_ssh_ok(cfg):
    """A remote command must exit 0 (e.g. 'is this container running')."""
    code, out, err = run_cmd(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", cfg["host"], cfg["command"]],
        SSH_TIMEOUT_S,
    )
    if code != 0:
        detail = (err or out or f"exit {code}").splitlines()[0][:200]
        return Result(cfg["name"], FAIL, detail)
    detail = out.splitlines()[0][:200] if out else "exit 0"
    return Result(cfg["name"], OK, detail)


CHECK_TYPES = {
    "pg_cron": check_pg_cron,
    "freshness": check_freshness,
    "http": check_http,
    "ssh_file_age": check_ssh_file_age,
    "ssh_ok": check_ssh_ok,
}


# ---------------------------------------------------------------- report

def build_report(results):
    now = datetime.now(timezone.utc).astimezone()
    fails = [r for r in results if r.status == FAIL]
    warns = [r for r in results if r.status == WARN]
    head = "\U0001f6f0 Fleet Watch " + now.strftime("%Y-%m-%d %H:%M")
    if fails:
        head += f" — {len(fails)} FAIL"
    elif warns:
        head += f" — {len(warns)} WARN"
    else:
        head += " — kaikki OK"
    lines = [head, ""]
    for r in results:
        lines.append(f"{ICON[r.status]} {r.name}: {r.detail}")
    return "\n".join(lines)


def send_telegram(token, chat_id, text):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    chunks = [text[i : i + TELEGRAM_CHUNK] for i in range(0, len(text), TELEGRAM_CHUNK)]
    for idx, chunk in enumerate(chunks, 1):
        data = urllib.parse.urlencode({"chat_id": chat_id, "text": chunk}).encode()
        req = urllib.request.Request(url, data=data)
        try:
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode())
                if not payload.get("ok"):
                    raise RuntimeError(f"telegram API error: {payload}")
        except Exception as exc:
            raise RuntimeError(f"chunk {idx}/{len(chunks)} failed: {exc}") from exc


# ---------------------------------------------------------------- main

def main():
    parser = argparse.ArgumentParser(description="Fleet Watch verification loop")
    parser.add_argument("--config", required=True, help="path to config.json")
    parser.add_argument("--dry-run", action="store_true", help="print report, skip Telegram")
    args = parser.parse_args()

    # systemd may run with a non-UTF-8 locale; the report contains emoji
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    with open(args.config, encoding="utf-8") as fh:
        config = json.load(fh)

    checks = config.get("checks") or []
    if not checks:
        print("ERROR: config has no checks defined", file=sys.stderr)
        return 2

    results = []
    for cfg in checks:
        if cfg.get("disabled"):
            results.append(Result(cfg.get("name", "?"), SKIP, "disabled in config"))
            continue
        fn = CHECK_TYPES.get(cfg.get("type"))
        if fn is None:
            results.append(Result(cfg.get("name", "?"), FAIL, f"unknown type {cfg.get('type')}"))
            continue
        try:
            results.append(fn(cfg))
        except Exception as exc:  # a broken check must never kill the report
            results.append(Result(cfg.get("name", "?"), FAIL, f"check crashed: {exc}"))

    report = build_report(results)
    print(report)

    if not args.dry_run:
        tg = config.get("telegram", {})
        token = os.environ.get(tg.get("token_env", "FLEET_TELEGRAM_BOT_TOKEN"), "")
        chat_id = os.environ.get(tg.get("chat_id_env", "FLEET_TELEGRAM_CHAT_ID"), "")
        if not token or not chat_id:
            print("ERROR: telegram token/chat_id env not set", file=sys.stderr)
            return 2
        try:
            send_telegram(token, chat_id, report)
        except Exception as exc:
            print(f"ERROR: failed to send telegram report: {exc}", file=sys.stderr)
            return 2

    return 1 if any(r.status == FAIL for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
