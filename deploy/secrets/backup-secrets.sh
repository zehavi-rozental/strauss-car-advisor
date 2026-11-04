#!/usr/bin/env bash
# Portable copy of the DPAPI secret store: every secret (name, service, role, value) as JSON, encrypted with a
# passphrase you choose (AES-256, gpg). Needed because DPAPI data opens only for this Windows user on this computer.
# The plaintext exists only in a pipe, never in a file. Restore anywhere with:  gpg -d strauss-secrets-<date>.json.gpg
# Usage (Git Bash):  bash ~/strauss-secrets/backup-secrets.sh
set -euo pipefail
umask 077
OUT_DIR="${OUT_DIR:-$HOME/strauss-backups}"; mkdir -p "$OUT_DIR"
OUT="$OUT_DIR/strauss-secrets-$(date -u +%Y%m%dT%H%M%SZ).json.gpg"
export_json() {
  powershell.exe -NoProfile -Command '. C:/Users/user/strauss-secrets/secrets.ps1; Export-StraussSecretsJson' | tr -d '[:cntrl:]'
}
read -rsp "Passphrase for the secrets backup (hidden, 12+ chars, keep it in your password manager): " P1; echo
read -rsp "Repeat it: " P2; echo
[ "$P1" = "$P2" ] && [ "${#P1}" -ge 12 ] || { echo "ERROR: the passphrases differ or are shorter than 12 characters" >&2; exit 1; }
P2=""
n=$(export_json | grep -o '"name":' | wc -l)
[ "$n" -ge 1 ] || { echo "ERROR: the secret store could not be read" >&2; exit 1; }
want=$(export_json | sha256sum | cut -d' ' -f1)
export_json | gpg --batch --yes --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 \
  --symmetric --cipher-algo AES256 -o "$OUT" 3<<<"$P1"
got=$(gpg --batch --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 -d "$OUT" 3<<<"$P1" | sha256sum | cut -d' ' -f1)
P1=""
[ "$got" = "$want" ] || { rm -f "$OUT"; echo "ERROR: the encrypted copy does not decrypt to the store" >&2; exit 1; }
echo "OK: $n secrets encrypted and verified -> $OUT"
echo "Copy this file to a USB drive or a private cloud folder; keep the passphrase in your password manager."
