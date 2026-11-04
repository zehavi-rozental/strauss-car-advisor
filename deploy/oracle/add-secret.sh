#!/usr/bin/env bash
# Stores one secret you type (hidden) in the DPAPI store. Usage: bash add-secret.sh <name> "<service>" "<role>"
set -euo pipefail; umask 077
NAME="${1:?name}"; SERVICE="${2:?service}"; ROLE="${3:?role}"
read -rsp "Value for $NAME (hidden): " V; echo
[ -n "$V" ] || { echo "ERROR: empty" >&2; exit 1; }
TMP=$(mktemp); printf '%s' "$V" > "$TMP"; V=""
powershell.exe -NoProfile -Command ". C:/Users/user/strauss-secrets/secrets.ps1; Add-StraussSecret -Name '$NAME' -Service '$SERVICE' -Role '$ROLE' -ValueFile '$(cygpath -w "$TMP")'"
rm -f "$TMP"
