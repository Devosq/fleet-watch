# Fleet Watch

Deterministic morning verification loop for the Ubuntu worker machine.
Runs read-only checks against production infrastructure and sends one
Telegram report daily at 07:00 Helsinki. No LLM calls, zero cost per run.

Built 2026-06-10. Motivation: the oscar-csuite scout cron was silently
broken 2026-05-21..06-04 and TJ crons failed for weeks before anyone
noticed. This catches that class of failure within one day.

## Check types

| Type | What it verifies |
|------|------------------|
| `pg_cron` | Every active cron.job: last run not failed, ran recently (per-job `stale_after_hours`), jobs in `expect_active` exist and are active. Hung `running` jobs are caught by staleness. |
| `freshness` | A scalar timestamp SQL (e.g. `SELECT max(created_at) FROM opportunities`) is newer than `max_age_hours`. Future timestamps → WARN. |
| `http` | GET returns expected status (+ optional body substring). Prefer Uptime Kuma for plain HTTP; use this only for checks Kuma can't do. |
| `ssh_file_age` | Newest file under a remote path (e.g. backup dir on vps2) younger than `max_age_hours`. Requires GNU find on the remote. |
| `ssh_ok` | Remote command exits 0 (e.g. container running). |

Exit codes: 0 = all OK, 1 = at least one FAIL (report still sent), 2 = config/report delivery error.

## Deploy on the Ubuntu machine

```bash
sudo mkdir -p /opt/fleet-watch
sudo cp fleet_watch.py test_fleet_watch.py config.example.json /opt/fleet-watch/
sudo cp fleet-watch.env.example /opt/fleet-watch/fleet-watch.env
sudo cp fleet-watch.service fleet-watch.timer /etc/systemd/system/

# 1. Fill secrets (Telegram token/chat_id, Supabase pooler URIs)
sudo nano /opt/fleet-watch/fleet-watch.env
sudo chmod 600 /opt/fleet-watch/fleet-watch.env

# 2. Create config.json from the example; verify jobnames against prod:
#    psql "$CSUITE_DB_URL" -c "SELECT jobname, active FROM cron.job;"
sudo cp /opt/fleet-watch/config.example.json /opt/fleet-watch/config.json
sudo nano /opt/fleet-watch/config.json
sudo chmod 600 /opt/fleet-watch/config.json   # contains trusted SQL — keep it locked

# 3. Set the real username in fleet-watch.service (User=), then:
sudo apt-get install -y postgresql-client   # if psql missing
cd /opt/fleet-watch && python3 -m unittest test_fleet_watch   # 26 tests
set -a; source fleet-watch.env; set +a
python3 fleet_watch.py --config config.json --dry-run          # verify before Telegram

# 4. Enable the TIMER (not the service)
sudo systemctl daemon-reload
sudo systemctl enable --now fleet-watch.timer
systemctl list-timers fleet-watch.timer
```

## Security notes

- All DB access is forced read-only (`default_transaction_read_only=on`) with a 20 s statement timeout.
- `timestamp_sql` in config.json is trusted operator input (single statement enforced, but arbitrary SELECT possible) — that is why config.json must be `chmod 600`.
- Connection strings live only in `fleet-watch.env` (600) and are never printed; psql errors are truncated to one line.
- Known limitation: the connection string is visible in the local process table during the ~seconds-long psql run. Acceptable on a single-user machine.

## Adding a check

Append an object to `checks` in config.json. Set `"disabled": true` to park a
check without deleting it (reported as SKIP).
