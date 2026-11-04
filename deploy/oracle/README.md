# Production: Cloudflare Worker → Oracle Cloud VM → Neon

Public link: https://strauss-drive.strauss-drive-edge.workers.dev/employee

```
Browser ─HTTPS─> Cloudflare Worker (edge/: React assets + /api proxy, EDGE_PROXY_SECRET)
                   └─HTTPS─> strauss-drive.duckdns.org = Oracle VM "strauss-drive-prod" (A1.Flex 1 OCPU / 6 GB, Always Free)
                               Caddy (Let's Encrypt) ─> app container (repo Dockerfile) ─TLS─> Neon PostgreSQL (main)
```

## Files

| File | Role |
| --- | --- |
| `launch_vm.py` | Launches the VM with a cloud-init that installs Docker, updates DuckDNS, writes `/srv/strauss/secrets/app.env` (mode 600), downloads the code bundle, starts the stack and runs the self-test. Env: `VM_NAME`, `DB_SECRET` (`neon-database-url` for production, `neon-vm-test-url` for staging), `VM_STATE`, `EMAIL_MODE=smtp` (optional). |
| `oci_ops.py` | OCI API helpers (config in `~/strauss-secrets/oci/config`); `status` lists the VM, plugins and ingress rules. |
| `docker-compose.yml`, `Caddyfile` | The stack on the VM; `restart: unless-stopped`, a Docker healthcheck and a cron that restarts an unhealthy app. |
| `vm-setup-and-test.sh` | Runs on the VM at boot: host checks, `compose up`, HTTPS for each name, `e2e_api.py`, `app.verify`. |
| `e2e_api.py` | End-to-end suite against any base URL (`python e2e_api.py <url> <payslip.pdf>`, env `REP_TOKEN`). Make the synthetic PDF with `node render_payslip.cjs <repo>/frontend <out.pdf>`. |
| `add-secret.sh` | Stores one secret typed hidden (Git Bash); in PowerShell use `Add-StraussSecretSecure` from `deploy/secrets/secrets.ps1`. |

## Deploy (immutable: a new VM each time; the VM holds no data)

1. Build the code bundle (`git archive HEAD` + the files above + the synthetic PDF) and upload it to the private bucket `strauss-deploy` with fresh read/write pre-authenticated links (`~/strauss-edge-server/.par-code`, `.par-results`).
2. Terminate the running `strauss-drive-prod` (Always Free allows 2 OCPU in total), then `VM_NAME=strauss-drive-prod DB_SECRET=neon-database-url python launch_vm.py`.
3. Read `results/vm-test.txt` from the bucket: it must end with `== ALL PASS`.
4. The Worker needs no change (DuckDNS follows the new VM). Run `e2e_api.py` against the public link.

Worker: `cd edge && npx wrangler@4.156.2 deploy --var BACKEND_ORIGIN:https://strauss-drive.duckdns.org` (the secret `EDGE_PROXY_SECRET` stays). Rollback of the edge: the previous version in the Cloudflare dashboard, or the same command with another origin.

## Network

Security list: 22 (SSH), 80 (ACME HTTP-01 and the redirect) from anywhere; 443 only from Cloudflare's IPv4 ranges (https://www.cloudflare.com/ips-v4). Cloudflare Workers could not resolve sslip.io / nip.io / traefik.me names (Origin DNS error 1016); DuckDNS works.

## Secrets (names only; values in the DPAPI store, backed up by `deploy/secrets/backup-secrets.sh`)

`edge-proxy-secret`, `edge-rep-access-token`, `edge-email-encryption-key`, `gemini-api-key`, `neon-database-url`, `neon-vm-test-url`, `duckdns-token`, `oracle-ssh-private-key`, `oci-api-private-key`, `smtp-host`, `smtp-port`, `smtp-username`, `smtp-password`, `smtp-from`.

## Backups

`deploy/backup/`: `render-backup.sh` (pg_dump over verify-full TLS with the pinned ISRG Root X1, test restore, AES-256) and `restore-to-neon.sh` (into an empty Neon database, then counts and content md5 against a reference restore). Neon Free also keeps 6 hours of history.
