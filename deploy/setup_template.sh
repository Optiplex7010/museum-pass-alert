#!/usr/bin/env bash
# One-shot installer: puts the museum pass scanner on this Ubuntu/Debian server and
# runs it EVERY MINUTE with cron. Run it once:   bash setup.sh
set -euo pipefail

APP="$HOME/museum-pass-alert"
mkdir -p "$APP"

echo "1/5 Installing Python and time-zone data (may ask for your sudo password)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 tzdata cron util-linux >/dev/null

echo "2/5 Writing the scanner to $APP/monitor.py ..."
base64 -d > "$APP/monitor.py" <<'B64'
@@PAYLOAD@@
B64
chmod 700 "$APP/monitor.py"

echo "3/5 Saving your Gmail login (stays on this server, readable only by you)..."
read -r -p "Gmail address to send from and to: " GMAIL_USER
read -r -s -p "Gmail app password (typing is hidden): " GMAIL_APP_PASSWORD
echo
umask 077
printf 'GMAIL_USER=%s\nGMAIL_APP_PASSWORD=%s\n' "$GMAIL_USER" "$GMAIL_APP_PASSWORD" > "$APP/.env"
chmod 600 "$APP/.env"

cat > "$APP/run.sh" <<'RUN'
#!/usr/bin/env bash
cd "$(dirname "$0")"
set -a; . ./.env; set +a
python3 monitor.py >> run.log 2>&1
# keep the log small: last 3000 lines
if [ "$(wc -l < run.log)" -gt 6000 ]; then tail -n 3000 run.log > run.log.tmp && mv run.log.tmp run.log; fi
RUN
chmod 700 "$APP/run.sh"

echo "4/5 Sending a test email..."
( cd "$APP"; set -a; . ./.env; set +a; python3 monitor.py --test-email )
echo "    Check your inbox (and spam) for: TEST museum pass alert"

echo "5/5 Scheduling a scan every minute..."
LINE="* * * * * flock -n /tmp/museum-pass.lock $APP/run.sh"
( crontab -l 2>/dev/null | grep -v 'museum-pass' ; echo "$LINE" ) | crontab -
sudo systemctl enable --now cron >/dev/null 2>&1 || true

echo
echo "Done. It now scans every minute, even when your own computer is off."
echo "Watch it:   tail -f $APP/run.log"
echo "Remember to DISABLE the GitHub workflow, or you will get duplicate emails."
