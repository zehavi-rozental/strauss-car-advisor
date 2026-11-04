#!/usr/bin/env bash
# Runs ON the VM (sent by cloudshell-run.sh). Checks the host, starts the stack, and prints test results only
# (no secret is ever printed). Database: whatever DATABASE_URL secrets/app.env holds (the Neon vm-test branch).
set -uo pipefail
cd /srv/strauss
ok() { printf 'PASS  %s\n' "$*"; }; bad() { printf 'FAIL  %s\n' "$*"; }
IP=$(curl -s -m 5 https://ifconfig.me || hostname -I | cut -d' ' -f1); D="${IP//./-}"; DOMAIN="strauss-drive.duckdns.org"
# the Worker's origin: strauss-drive.duckdns.org (resolves at Cloudflare; updated to this VM at boot). The two
# wildcard names stay as spares with their own certificates.
printf 'SITE_DOMAIN=%s, %s.nip.io, %s.sslip.io
' "$DOMAIN" "$D" "$D" > /srv/strauss/.env
export SITE_DOMAIN="$DOMAIN, $D.nip.io, $D.sslip.io"
echo "== host"
s=$(cloud-init status 2>&1 | head -1); case "$s" in *done*) ok "cloud-init done";; *running*) ok "cloud-init running (this test is its last step)";; *) bad "cloud-init: $s";; esac
docker version --format '{{.Server.Version}} {{.Server.Arch}}' >/dev/null 2>&1 && ok "docker $(docker version --format '{{.Server.Version}} {{.Server.Arch}}')" || bad "docker not running"
docker compose version >/dev/null 2>&1 && ok "$(docker compose version)" || bad "docker compose missing"
for s in fail2ban unattended-upgrades; do systemctl is-active -q $s && ok "$s active" || bad "$s not active"; done
sudo iptables -S INPUT | grep -q 'dport 443 -j ACCEPT' && ok "iptables: 80/443 open" || bad "iptables: 443 not open"
[ "$(stat -c %a secrets/app.env)" = 600 ] && ok "secrets/app.env mode 600" || bad "secrets/app.env mode $(stat -c %a secrets/app.env)"
free -m | awk 'NR==2 {printf "INFO  memory: %s MB total, %s MB used\n", $2, $3}'
echo "== build and start (first build takes a few minutes)"
docker compose up -d --build --quiet-pull > /tmp/compose.log 2>&1 && ok "compose up" || { bad "compose up"; tail -20 /tmp/compose.log | grep -viE 'key|token|password|secret'; exit 1; }
for i in $(seq 1 60); do docker compose ps app | grep -q healthy && break; sleep 5; done
docker compose ps app | grep -q healthy && ok "app healthy" || { bad "app not healthy"; docker compose logs --tail 30 app | sed -E 's#postgres(ql)?://[^ ]+#<db-url>#g; s#ep-[a-z0-9-]+#<neon-endpoint>#g; s#[A-Za-z0-9_-]{30,}#<long-value>#g' | grep -viE 'key|token|password|secret'; exit 1; }
echo "== HTTPS ($DOMAIN)"
for n in "$DOMAIN" "$D.nip.io" "$D.sslip.io"; do
  for i in $(seq 1 30); do curl -sf -m 10 --resolve "$n:443:127.0.0.1" "https://$n/api/health" >/dev/null && break; sleep 5; done
  curl -sf -m 10 --resolve "$n:443:127.0.0.1" "https://$n/api/health" >/dev/null && ok "https://$n (valid certificate)" || bad "HTTPS for $n"
done
echo "== end-to-end (e2e_api.py inside the app container, through Caddy + TLS)"
APP=$(docker compose ps -q app)
docker cp /srv/strauss/e2e_api.py "$APP":/tmp/e2e_api.py >/dev/null && docker cp /srv/strauss/synthetic_payslip.pdf "$APP":/tmp/p.pdf >/dev/null
docker compose exec -T -u root app chmod 644 /tmp/e2e_api.py /tmp/p.pdf
docker compose exec -T app python /tmp/e2e_api.py "https://$DOMAIN" /tmp/p.pdf 2>&1 | sed -E 's#postgres(ql)?://[^ ]+#<db-url>#g'
echo "== calculation engine on the VM"
docker compose exec -T app python -m app.verify 2>&1 | tail -1
echo "== Oracle Cloud Agent (why Run Command does not pick up commands)"
snap services oracle-cloud-agent 2>/dev/null | tail -n +2 | awk '{print "INFO  agent service:", $1, $3}'
for f in /var/log/oracle-cloud-agent/agent.log /var/log/oracle-cloud-agent/plugins/runcommand/runcommand.log; do
  [ -f "$f" ] && sudo tail -n 4 "$f" | cut -c1-160 | sed -E 's#ocid1\.[a-z0-9.]+#<ocid>#g; s#[A-Za-z0-9+/=_-]{40,}#<long>#g' | sed "s#^#INFO  $(basename "$f"): #"
done
# restart the agent so it reports again (no extra permission is given to the Run Command user)
sudo snap restart oracle-cloud-agent >/dev/null 2>&1 && echo "INFO  oracle-cloud-agent restarted"
echo "== done"
