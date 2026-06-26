#!/usr/bin/env bash
# Fleet Watch -> VPS2 stage-deploy.
# Idempotent. Does NOT enable the timer or touch secrets — that stays manual.
# Run from git-bash on the workstation:  bash ~/oscar-tasks/fleet-watch/deploy-to-vps2.sh
set -euo pipefail
cd "$HOME/oscar-tasks/fleet-watch"

echo "[1/6] Create /opt/fleet-watch and copy code + units to VPS2..."
ssh vps2 'mkdir -p /opt/fleet-watch'
scp -q fleet_watch.py test_fleet_watch.py config.example.json fleet-watch.env.example vps2:/opt/fleet-watch/
scp -q fleet-watch.service fleet-watch.timer vps2:/etc/systemd/system/

echo "[2/6] Install postgresql-client (psql) for DB checks..."
ssh vps2 'apt-get update -qq && DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql-client >/dev/null; psql --version'

echo "[3/6] Create dedicated unprivileged fleetwatch user + harden service + daemon-reload..."
ssh vps2 'id -u fleetwatch >/dev/null 2>&1 || useradd --system --no-create-home --shell /usr/sbin/nologin fleetwatch'
ssh vps2 "sed -i 's/^User=oscar/User=fleetwatch/' /etc/systemd/system/fleet-watch.service"
ssh vps2 "sed -i 's/^ProtectHome=read-only/ProtectHome=true/' /etc/systemd/system/fleet-watch.service"
ssh vps2 'systemctl daemon-reload'

echo "[4/6] Seed config.json + placeholder env (chmod 600, owned by fleetwatch), without overwriting existing..."
ssh vps2 'cd /opt/fleet-watch && { [ -f config.json ] || cp config.example.json config.json; } && { [ -f fleet-watch.env ] || cp fleet-watch.env.example fleet-watch.env; } && chmod 600 config.json fleet-watch.env && chown -R fleetwatch:fleetwatch /opt/fleet-watch && echo "config.json + fleet-watch.env present, owned by fleetwatch"'

echo "[5/6] Run the 26 unit tests on VPS2 (mocked external calls)..."
ssh vps2 'cd /opt/fleet-watch && python3 -m unittest test_fleet_watch 2>&1 | tail -4'

echo "[6/6] State (timer NOT enabled — waiting on secrets):"
ssh vps2 'systemctl list-unit-files fleet-watch.timer --no-pager 2>&1 | head -3; echo "---"; ls -la /opt/fleet-watch'

cat <<'EOF'

DONE — code, deps and tests are live on VPS2; the timer is staged but NOT enabled.
To activate (after filling secrets), run these yourself:

  ssh vps2 'nano /opt/fleet-watch/fleet-watch.env'   # FLEET_TELEGRAM_BOT_TOKEN, FLEET_TELEGRAM_CHAT_ID, CSUITE_DB_URL, TJ_DB_URL
  ssh vps2 'nano /opt/fleet-watch/config.json'       # fill expect_active[] from: SELECT jobname FROM cron.job;

  # dry-run first as the fleetwatch user (prints report, no Telegram send):
  ssh vps2 "cd /opt/fleet-watch && sudo -u fleetwatch bash -c 'set -a; . ./fleet-watch.env; set +a; python3 fleet_watch.py --config config.json --dry-run'"

  # then enable the daily 07:00 Helsinki timer:
  ssh vps2 'systemctl enable --now fleet-watch.timer && systemctl list-timers fleet-watch.timer --no-pager'
EOF
