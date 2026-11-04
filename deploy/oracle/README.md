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
| `mail_check.py` | Run by the self-test: with `EMAIL_BACKEND=smtp`, checks the mail settings and an SMTP login (sends nothing, prints no address or password). |
| `e2e_api.py` | End-to-end suite against any base URL (`python e2e_api.py <url> <payslip.pdf>`, env `REP_TOKEN`). Make the synthetic PDF with `node render_payslip.cjs <repo>/frontend <out.pdf>`. |
| `add-secret.sh` | Stores one secret typed hidden (Git Bash); in PowerShell use `Add-StraussSecretSecure` from `deploy/secrets/secrets.ps1`. |

## Deploy (immutable: a new VM each time; the VM holds no data)

1. Build the code bundle (`git archive HEAD` + the files above + the synthetic PDF) and upload it to the private bucket `strauss-deploy` with fresh read/write pre-authenticated links (`~/strauss-edge-server/.par-code`, `.par-results`).
2. Terminate the running `strauss-drive-prod` (Always Free allows 2 OCPU in total), then `VM_NAME=strauss-drive-prod DB_SECRET=neon-database-url python launch_vm.py`.
3. Read `results/vm-test.txt` from the bucket: it must end with `== ALL PASS` and have no `FAIL` line (the mail lines included).
4. The Worker needs no change (DuckDNS follows the new VM). Run `e2e_api.py` against the public link.

Worker: `cd edge && npx wrangler@4.156.2 deploy --var BACKEND_ORIGIN:https://strauss-drive.duckdns.org` (the secret `EDGE_PROXY_SECRET` stays). Rollback of the edge: the previous version in the Cloudflare dashboard, or the same command with another origin.

## Turning mail on (the existing flow: verification link, outbox, the rep's answer with a link to it)

Sender: a dedicated Gmail account over SMTP (free, any recipient, signed by Google; about 500 recipients a day, enough for the demo). Resend's test sender `@resend.dev` delivers to the Resend account owner only; production never sent mail before.

1. Secrets (PowerShell, after `. C:\Users\user\strauss-secrets\secrets.ps1`; each value is typed hidden). They replace the Resend values of the same names, so run the secrets backup first and again after:
   - `Add-StraussSecretSecure smtp-host "Gmail" "SMTP host"` → `smtp.gmail.com`
   - `Add-StraussSecretSecure smtp-port "Gmail" "SMTP port"` → `587`
   - `Add-StraussSecretSecure smtp-username "Gmail" "SMTP user"` → the Gmail address
   - `Add-StraussSecretSecure smtp-password "Gmail" "app password"` → the 16-letter app password, without spaces
   - `Add-StraussSecretSecure smtp-from "Gmail" "sender"` → the same Gmail address (Gmail rewrites any other sender; the app names it "STRAUSS DRIVE")
2. Code bundle from `main` as above (it includes `mail_check.py`), then terminate `strauss-drive-prod` and launch with mail: `VM_NAME=strauss-drive-prod DB_SECRET=neon-database-url EMAIL_MODE=smtp python launch_vm.py`.
3. `results/vm-test.txt` must show `PASS  mail settings`, `PASS  Gmail: sender is the signed-in account`, `PASS  public link is https`, `PASS  SMTP login (nothing sent)`, no `FAIL`, and `== ALL PASS`.
4. One real test through the public link, to an approved address: ask the rep a question, enter the address, open the verification mail, answer on the rep screen, open the answer mail's link (it must open that answer in the chat).

Rollback (about 5 minutes, nothing is lost): terminate and launch again without `EMAIL_MODE` (= `EMAIL_BACKEND=disabled`). Inquiries and answers keep working in the chat; mail waiting in the outbox (Neon) stays there unsent. A send error never rolls back a rep answer: the outbox retries with back-off (at most 8 attempts), then marks it FAILED. A Gmail block or a revoked app password shows as `FAIL  SMTP login` (the error class only).

Verification links: one-time, 24 h; a new one on request only after 2 minutes, at most 5 per conversation and address.

## Network

Security list: 22 (SSH), 80 (ACME HTTP-01 and the redirect) from anywhere; 443 only from Cloudflare's IPv4 ranges (https://www.cloudflare.com/ips-v4). Cloudflare Workers could not resolve sslip.io / nip.io / traefik.me names (Origin DNS error 1016); DuckDNS works.

## Secrets (names only; values in the DPAPI store, backed up by `deploy/secrets/backup-secrets.sh`)

`edge-proxy-secret`, `edge-rep-access-token`, `edge-email-encryption-key`, `gemini-api-key`, `neon-database-url`, `neon-vm-test-url`, `duckdns-token`, `oracle-ssh-private-key`, `oci-api-private-key`, `smtp-host`, `smtp-port`, `smtp-username`, `smtp-password`, `smtp-from`.

Back up the store after every new or rotated secret (PowerShell): `& "C:\Program Files\Git\bin\bash.exe" -lc "bash ~/strauss-secrets/backup-secrets.sh"` (gpg AES-256; asks for its passphrase). The backup must be newer than the newest secret.

## Backups

`deploy/backup/`: `render-backup.sh` (pg_dump over verify-full TLS with the pinned ISRG Root X1, test restore, AES-256) and `restore-to-neon.sh` (into an empty Neon database, then counts and content md5 against a reference restore). Neon Free also keeps 6 hours of history.
