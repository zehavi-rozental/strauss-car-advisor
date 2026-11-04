#!/usr/bin/env bash
# Restores a verified encrypted backup (render-backup.sh) into the EMPTY Neon database, then proves the copy:
# the same backup is also restored into a throwaway local database held in memory, and every table's row count and
# content md5 must be equal in both. Nothing is written to Render. Only counts and hashes are logged.
# Usage (Git Bash, Docker Desktop running):  bash restore-to-neon.sh ~/strauss-backups/render-<stamp>.dump.gpg
set -euo pipefail
umask 077
ENC="${1:?give the .dump.gpg file}"
HERE="$(cd "$(dirname "$0")" && pwd)"
IMAGE=postgres:18
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TAG="strauss-restore-$STAMP"
LOG="$HOME/strauss-backups/neon-restore-$STAMP.log"
CA_FILE="$HERE/isrg-root-x1.pem"
CA_SHA256="96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6"
export MSYS_NO_PATHCONV=1
DBPASS=""; P1=""

die() { echo "ERROR: $*" >&2; exit 1; }
cleanup() {
  local rc=$?
  trap - INT TERM
  local ids; ids=$(docker ps -aq --filter "name=$TAG-" 2>/dev/null || true)
  [ -z "$ids" ] || docker rm -fv $ids >/dev/null 2>&1 || true
  DBPASS=""; P1=""
  [ "$rc" -eq 0 ] || echo "FAILED (exit $rc). Nothing was left running." >&2
}
trap cleanup EXIT
trap 'exit 130' INT TERM

[ -f "$ENC" ] && [ -f "$ENC.plain-sha256" ] || die "missing $ENC or its .plain-sha256"
FP=$(openssl x509 -noout -fingerprint -sha256 < "$CA_FILE" 2>/dev/null | cut -d= -f2 | tr -cd '0-9A-F:' || true)
[ "$FP" = "$CA_SHA256" ] || die "$CA_FILE is not ISRG Root X1"
docker info >/dev/null 2>&1 || die "Docker Desktop is not running"
docker pull -q "$IMAGE" >/dev/null || die "cannot pull $IMAGE"

# the Neon connection string from the encrypted store (DPAPI); never printed
URL=$(powershell.exe -NoProfile -Command ". C:\Users\user\strauss-secrets\secrets.ps1; Get-StraussSecretValue ${NEON_SECRET:-neon-database-url}" | tr -d '\r')
re='^postgres(ql)?://([^:/@]+):([^@]+)@([^:/?]+)(:([0-9]+))?/([^?]+)(\?.*)?$'
[[ "$URL" =~ $re ]] || { URL=""; die "the stored Neon URL is not in the expected form"; }
export PGUSER="${BASH_REMATCH[2]}" PGHOST="${BASH_REMATCH[4]}" PGPORT="${BASH_REMATCH[6]:-5432}" PGDATABASE="${BASH_REMATCH[7]}"
DBPASS="${BASH_REMATCH[3]}"; URL=""; [[ x =~ x ]]
[[ "$PGHOST" != *-pooler* ]] || die "use the direct (not -pooler) Neon connection for a restore"
export PGSSLMODE=verify-full PGSSLROOTCERT=/run/strauss-ca.pem PGAPPNAME=strauss-restore
CA_MOUNT=(-v "$(cygpath -w "$CA_FILE"):/run/strauss-ca.pem:ro")

read -rsp "Passphrase of the backup (hidden): " P1; echo
want=$(cat "$ENC.plain-sha256")
got=$(gpg --batch --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 -d "$ENC" 3<<<"$P1" | sha256sum | cut -d' ' -f1) \
  || die "the backup does not decrypt (wrong passphrase?)"
[ "$got" = "$want" ] || die "the decrypted backup does not match its recorded sha256"
echo "1/5 backup decrypts and matches its sha256"

# neon <step> <command...>: a client container; the Neon password is the first line of its stdin, the rest follows
neon() {
  local step="$1"; shift
  docker run --rm -i --name "$TAG-$step" "${CA_MOUNT[@]}" -e PGHOST -e PGPORT -e PGUSER -e PGDATABASE -e PGSSLMODE \
    -e PGSSLROOTCERT -e PGAPPNAME "$IMAGE" sh -c 'IFS= read -r PGPASSWORD; export PGPASSWORD; exec "$@"' sh "$@"
}

n=$( { printf '%s\n' "$DBPASS"; } | neon empty psql -X -At -v ON_ERROR_STOP=1 -c \
  "select count(*) from information_schema.tables where table_schema = 'public'") || die "cannot connect to Neon"
[ "$n" = 0 ] || die "the Neon database is not empty ($n tables): nothing restored, nothing overwritten"
echo "2/5 Neon reachable over verified TLS, database empty"

{ printf '%s\n' "$DBPASS"
  gpg --batch --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 -d "$ENC" 3<<<"$P1"
} | neon restore pg_restore -d "$PGDATABASE" --no-owner --no-acl --exit-on-error --single-transaction \
  || die "pg_restore into Neon failed (single transaction: nothing was kept)"
echo "3/5 restored into Neon"

C="$TAG-ref"
docker run -d --rm --name "$C" --network none --tmpfs /pgtmp:rw,size=1024m -e PGDATA=/pgtmp/data \
  -e POSTGRES_PASSWORD=local-only -e POSTGRES_DB=ref "$IMAGE" >/dev/null
for i in $(seq 1 90); do docker exec "$C" pg_isready -q -h 127.0.0.1 -U postgres -d ref 2>/dev/null && break; sleep 2; done
gpg --batch --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 -d "$ENC" 3<<<"$P1" \
  | docker exec -i "$C" pg_restore -U postgres -d ref --no-owner --no-acl --exit-on-error --single-transaction \
  || die "reference restore failed"
P1=""
echo "4/5 reference copy restored locally (memory only)"

docker exec -i "$C" psql -X -q -At -F' ' -U postgres -d ref -f - < "$HERE/fingerprint.sql" > "$LOG.ref"
{ printf '%s\n' "$DBPASS"; cat "$HERE/fingerprint.sql"; } | neon fp psql -X -q -At -F' ' -f - > "$LOG.neon"
DBPASS=""
{ echo "table rows content_md5 (reference | neon)"; paste -d'|' "$LOG.ref" "$LOG.neon"; } > "$LOG"
rm -f "$LOG.ref"
if [ -s "$LOG.neon" ] && diff -q <(cut -d'|' -f1 "$LOG" | tail -n +2) <(cut -d'|' -f2 "$LOG" | tail -n +2) >/dev/null; then
  rm -f "$LOG.neon"
  echo "5/5 OK: $(($(wc -l < "$LOG") - 1)) tables, row counts and content md5 identical in Neon and in the backup"
  echo "    report (counts and hashes only): $LOG"
else
  rm -f "$LOG.neon"; die "Neon differs from the backup: see $LOG"
fi
