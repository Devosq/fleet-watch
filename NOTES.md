# Fleet Watch — operator notes

Discovery queries for filling the `FILL:` markers in `config.json`.
All are read-only `SELECT`s, safe to run against prod. Run from a machine
where the connection-string env vars are exported (see `fleet-watch.env`).

## 1. Discover pg_cron jobnames (fills `expect_active` + `stale_after_hours`)

Run once per database (csuite and TJ have separate `cron.job` tables):

```bash
# oscar-csuite (wtmfrxwidtxyhjgmptuj)
psql "$CSUITE_DB_URL" -c "SELECT jobname, active FROM cron.job ORDER BY jobname;"

# Xyven TJ
psql "$TJ_DB_URL" -c "SELECT jobname, active FROM cron.job ORDER BY jobname;"
```

Plain SQL (paste into psql / Supabase SQL editor):

```sql
SELECT jobname, active FROM cron.job ORDER BY jobname;
```

- Put every job that should be running into the check's `expect_active` array.
- Do NOT list `csuite-dipwork-daily-outreach` — it is intentionally
  `active = false` until the owner enables it.
- Add a per-job staleness limit in `stale_after_hours`, e.g.
  `{"csuite-scout": 30}` (hours since last run before it is flagged).

Optional — see schedule + last run while you're there:

```sql
SELECT j.jobname, j.active, j.schedule,
       d.status AS last_status,
       d.start_time AS last_run
FROM cron.job j
LEFT JOIN LATERAL (
  SELECT status, start_time FROM cron.job_run_details
  WHERE jobid = j.jobid ORDER BY start_time DESC LIMIT 1
) d ON true
ORDER BY j.jobname;
```

## 2. Confirm the churn_scores timestamp column (fills `FILL_TS_COLUMN`)

The TJ churn_scores freshness check needs the real timestamp column
(`updated_at` vs `created_at`). Discover it:

```bash
psql "$TJ_DB_URL" -c "SELECT column_name, data_type FROM information_schema.columns WHERE table_name='churn_scores' AND data_type LIKE 'timestamp%' ORDER BY column_name;"
```

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_name = 'churn_scores'
  AND data_type LIKE 'timestamp%'
ORDER BY column_name;
```

Then replace `FILL_TS_COLUMN` in `config.json`, e.g.
`SELECT max(updated_at) FROM churn_scores`.

## 3. Backup path (fills `ssh_file_age` path)

Find the real offsite backup directory on VPS2, then set it in the
`addwork-prod offsite backup` check and remove `"disabled": true`:

```bash
ssh vps2 'ls -lt /opt/backups 2>/dev/null; find / -maxdepth 4 -type d -iname "*backup*" 2>/dev/null | head'
```

## After filling

```bash
# Dry-run (prints report, sends nothing) once envs are exported:
set -a; source fleet-watch.env; set +a
python3 fleet_watch.py --config config.json --dry-run
```
