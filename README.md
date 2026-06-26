# Fleet Watch

> A deterministic **morning verification loop** for your infrastructure. Runs
> read-only checks against databases and servers and sends **one Telegram report**
> a day. No LLM, no cost per run — it just answers "did everything that should
> have run, actually run?"

![CI](https://github.com/Devosq/fleet-watch/actions/workflows/ci.yml/badge.svg)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

Uptime monitors tell you a URL is up. They don't tell you a nightly `pg_cron`
job died, an ingestion table stopped getting rows, or an offsite backup went
stale. Fleet Watch catches that class of **silent** failure — usually within a
day — with a few lines of config.

## Check types
| Type | What it verifies |
|------|------------------|
| `pg_cron` | Each active `cron.job`: last run not failed, ran recently (per-job `stale_after_hours`), and every job in `expect_active` exists and is active. Hung jobs are caught by staleness. |
| `freshness` | A scalar timestamp SQL (e.g. `SELECT max(created_at) FROM orders`) is newer than `max_age_hours`. Future timestamps → WARN. |
| `http` | GET returns the expected status (+ optional body substring). |
| `ssh_file_age` | Newest file under a remote path (e.g. a backup dir) is younger than `max_age_hours`. Requires GNU `find` on the remote. |
| `ssh_ok` | A remote command exits 0 (e.g. a container is running). |

## Quickstart
```bash
pip install -r requirements.txt        # only: requests
cp config.example.json config.json     # edit checks to match your infra
cp fleet-watch.env.example fleet-watch.env   # Telegram token + DB URLs
set -a; source fleet-watch.env; set +a
python fleet_watch.py --config config.json --dry-run   # verify before sending
```

Exit codes: `0` all OK · `1` at least one FAIL (report still sent) · `2`
config/delivery error.

## Configuration
Each entry in `checks[]` has a `type` and type-specific fields — see
[`config.example.json`](./config.example.json) for one of each. DB URLs and the
Telegram token are referenced by **env var name** (`db_url_env`, `token_env`), so
no secret lives in the config file. Set `"disabled": true` to park a check
(reported as SKIP).

## Deploy (systemd)
Units are included ([`fleet-watch.service`](./fleet-watch.service) +
[`fleet-watch.timer`](./fleet-watch.timer)) for a daily run:
```bash
sudo cp fleet_watch.py config.json /opt/fleet-watch/
sudo cp fleet-watch.{service,timer} /etc/systemd/system/
# set User= and EnvironmentFile path, then:
sudo systemctl enable --now fleet-watch.timer
```
The service is hardened (`ProtectSystem=strict`, `NoNewPrivileges`, read-only
home). Enable the **timer**, not the service.

## Security
- All DB access is forced **read-only** (`default_transaction_read_only=on`) with
  a 20 s statement timeout.
- `timestamp_sql` is trusted operator input (one statement enforced) — keep
  `config.json` at `chmod 600`.
- Connection strings live only in the env file (chmod 600) and are never printed;
  psql errors are truncated to one line.

## Development
```bash
python -m unittest discover -v   # 26 tests (external calls mocked)
ruff check .
```

## License
[MIT](./LICENSE) (c) Oscar Vatanen
