#!/usr/bin/env bash
# Read-only backup of the Render Free PostgreSQL:
#   dump over TLS in a server-enforced read-only session -> test-restore into a throwaway local database held in
#   memory (no network, no disk) -> compare row counts -> encrypt (AES-256) -> verify -> delete the plaintext.
# Windows: Git Bash + Docker Desktop (Linux containers).
# Usage:  bash render-backup.sh <client major version: at least 16 and at least the server's, e.g. 16>
# Only if the certificate check fails and you accept encryption without server verification:
#         PGSSLMODE=require bash render-backup.sh 16
#
# The database password never goes into an environment variable of a container, a command line or a file:
# it is written as the first line of the container's stdin and read there (docker inspect cannot show it).
set -euo pipefail
umask 077

PGVER="${1:-}"
[[ "$PGVER" =~ ^[0-9]{2}$ ]] && [ "$PGVER" -ge 16 ] \
  || { echo "usage: bash render-backup.sh <16 or the server's major version if newer>"; exit 2; }
HERE="$(cd "$(dirname "$0")" && pwd)"
SQL="$HERE/counts.sql"
OUT="${BACKUP_DIR:-$HOME/strauss-backups}"
IMAGE="postgres:$PGVER"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DUMP="$OUT/render-$STAMP.dump"
ENC="$DUMP.gpg"
TAG="strauss-backup-$STAMP"          # every container of this run is named $TAG-*, so cleanup finds them all
C="$TAG-restore"
DBPASS=""                            # a shell variable only: never exported, never passed with -e
# verify-full trusts exactly one root: Let's Encrypt's ISRG Root X1 (Render's certificates chain to it). The postgres
# image has no CA store of its own, so "system" verified nothing; the file is checked by its fingerprint first.
CA_FILE="$HERE/isrg-root-x1.pem"
CA_SHA256="96:BC:EC:06:26:49:76:F3:74:60:77:9A:CF:28:C5:A7:CF:E8:A3:C0:AA:E1:1A:8F:FC:EE:05:C0:BD:DF:08:C6"
CA_MOUNT=()
export MSYS_NO_PATHCONV=1

die() { echo "ERROR: $*" >&2; exit 1; }
cleanup() {
  local rc=$?
  trap - INT TERM
  local ids; ids=$(docker ps -aq --filter "name=$TAG-" 2>/dev/null || true)
  [ -z "$ids" ] || docker rm -fv $ids >/dev/null 2>&1 || true   # restore data lived in tmpfs: gone with it
  rm -f "$DUMP.part" "$DUMP"                                     # never leave a plaintext dump
  DBPASS=""; P1=""; P2=""
  [ "$rc" -eq 0 ] || echo "FAILED (exit $rc). No plaintext dump and no temporary container were left behind." >&2
}
trap cleanup EXIT
trap 'exit 130' INT TERM

# pgrun <step> <input file | -> <command...>: one client container named $TAG-<step> (unique per step, also when
# called inside $(...)); the password is the first line of its stdin.
pgrun() {
  local step="$1" input="$2"; shift 2
  { printf '%s\n' "$DBPASS"; if [ "$input" != - ]; then cat "$input"; fi; } \
    | docker run --rm -i --name "$TAG-$step" "${CA_MOUNT[@]}" -e PGHOST -e PGPORT -e PGUSER -e PGDATABASE \
        -e PGSSLMODE -e PGSSLROOTCERT -e PGOPTIONS -e PGAPPNAME "$IMAGE" \
        sh -c 'IFS= read -r PGPASSWORD; export PGPASSWORD; exec "$@"' sh "$@"
}

echo "0/7 preflight"
case "$OUT" in *OneDrive*|*Dropbox*|*"Google Drive"*) die "BACKUP_DIR is inside a synced folder: choose another";; esac
command -v docker >/dev/null || die "docker not found"
command -v gpg >/dev/null || die "gpg not found (it ships with Git for Windows)"
command -v openssl >/dev/null || die "openssl not found (it ships with Git for Windows)"
docker info >/dev/null 2>&1 || die "Docker Desktop is not running"
[ "$(docker info --format '{{.OSType}}')" = linux ] || die "Docker Desktop must use Linux containers"
docker pull -q "$IMAGE" >/dev/null || die "cannot pull $IMAGE (network / proxy?)"
mkdir -p "$OUT"

read -rsp "Paste the EXTERNAL Database URL (input hidden), then Enter: " URL; echo
re='^postgres(ql)?://([^:/@]+):([^@]+)@([^:/?]+)(:([0-9]+))?/([^?]+)(\?.*)?$'
[[ "$URL" =~ $re ]] || { URL=""; die "not a postgresql://user:password@host/db URL"; }
export PGUSER="${BASH_REMATCH[2]}" PGHOST="${BASH_REMATCH[4]}"
export PGPORT="${BASH_REMATCH[6]:-5432}" PGDATABASE="${BASH_REMATCH[7]}"
DBPASS="${BASH_REMATCH[3]}"
URL=""; [[ x =~ x ]]                             # overwrite BASH_REMATCH, which held the password
[[ "$PGHOST" == *.* ]] || die "this is the Internal URL: use the External one"
[[ "$DBPASS" != *%* ]] || die "the password is URL-encoded: not supported by this script"
export PGSSLMODE="${PGSSLMODE:-verify-full}"
case "$PGSSLMODE" in
  verify-full)
    [ -f "$CA_FILE" ] || die "missing $CA_FILE (keep it next to this script)"
    FP=$(openssl x509 -noout -fingerprint -sha256 < "$CA_FILE" 2>/dev/null | cut -d= -f2 | tr -cd '0-9A-F:' || true)
    [ "$FP" = "$CA_SHA256" ] || die "$CA_FILE is not ISRG Root X1 (fingerprint mismatch)"
    CA_MOUNT=(-v "$(cygpath -w "$CA_FILE" 2>/dev/null || echo "$CA_FILE"):/run/strauss-ca.pem:ro")
    export PGSSLROOTCERT=/run/strauss-ca.pem ;;   # the certificate chain and the host name are both verified
  require) unset PGSSLROOTCERT; echo "WARNING: TLS without verifying the server certificate (your explicit choice)" ;;
  *) die "PGSSLMODE must be verify-full (default) or require" ;;
esac
export PGOPTIONS="-c default_transaction_read_only=on" PGAPPNAME=strauss-backup

read -rsp "Passphrase for the encrypted backup (hidden, 12+ chars, keep it in a password manager): " P1; echo
read -rsp "Repeat the passphrase: " P2; echo
[ "$P1" = "$P2" ] && [ "${#P1}" -ge 12 ] || die "the passphrases differ or are shorter than 12 characters"
P2=""

echo "1/7 connect ($PGSSLMODE, read-only session)"
INFO=$(pgrun connect - psql -X -v ON_ERROR_STOP=1 -Atc "select current_setting('server_version_num')::int / 10000,
  current_setting('transaction_read_only'), (select ssl from pg_stat_ssl where pid = pg_backend_pid()),
  pg_database_size(current_database())") || die "cannot connect (message above)"
IFS='|' read -r SRV RO SSL SIZE <<<"$INFO"
[ "$RO" = on ] || die "the session is not read-only"
[ "$SSL" = t ] || die "the connection is not encrypted"
[ "$SRV" -le "$PGVER" ] || die "the server is PostgreSQL $SRV: run with $SRV"
echo "    server PostgreSQL $SRV, TLS on, read-only, database $(( SIZE / 1048576 )) MB"
FREE_KB=$(df -Pk "$OUT" | awk 'NR == 2 { print $4 }')
[ "$FREE_KB" -ge $(( SIZE / 1024 * 2 + 1048576 )) ] || die "not enough free disk space in $OUT"

COUNTS=(psql -X -q -v ON_ERROR_STOP=1 -At -F' ' -f -)

echo "2/7 row counts before"
pgrun counts-before "$SQL" "${COUNTS[@]}" > "$OUT/counts-before-$STAMP.txt" || die "count query failed"
echo "3/7 pg_dump (one consistent snapshot)"
pgrun dump - pg_dump -Fc --no-owner --no-acl > "$DUMP.part" || die "pg_dump failed"
[ -s "$DUMP.part" ] || die "the dump is empty"
mv "$DUMP.part" "$DUMP"
echo "4/7 row counts after"
pgrun counts-after "$SQL" "${COUNTS[@]}" > "$OUT/counts-after-$STAMP.txt" || die "count query failed"
DBPASS=""                                        # Render is not contacted again
sha256sum "$DUMP" | cut -d' ' -f1 > "$DUMP.sha256"

echo "5/7 restore into a throwaway local database (no network, data in memory only)"
TMPFS_MB=$(( SIZE / 1048576 * 3 + 512 ))
docker run -d --rm --name "$C" --network none --tmpfs "/pgtmp:rw,size=${TMPFS_MB}m" -e PGDATA=/pgtmp/data \
  -e POSTGRES_PASSWORD=local-only -e POSTGRES_DB=verify "$IMAGE" >/dev/null || die "local database failed to start"
for i in $(seq 1 90); do   # TCP: the entrypoint's temporary init server listens on the socket only
  docker exec "$C" pg_isready -q -h 127.0.0.1 -U postgres -d verify 2>/dev/null && break
  [ "$i" -eq 90 ] && die "the local database did not become ready"; sleep 2
done
docker exec -i "$C" pg_restore -U postgres -d verify --no-owner --no-acl --exit-on-error --single-transaction \
  < "$DUMP" || die "pg_restore failed"
docker exec -i -e PGUSER=postgres -e PGDATABASE=verify "$C" "${COUNTS[@]}" < "$SQL" \
  > "$OUT/counts-restored-$STAMP.txt" || die "count query on the restored copy failed"
docker rm -fv "$C" >/dev/null

echo "6/7 compare: each table's restored count must lie between its before and after counts"
awk 'FNR == 1 { f++ }
     { val[f, $1] = $2; keys[$1] = 1 }
     END {
       bad = 0
       for (t in keys) {
         b = val[1, t]; a = val[2, t]; r = val[3, t]
         if (t == "schema_version") ok = (r != "" && b == a && a == r)
         else { lo = (b + 0 < a + 0) ? b : a; hi = (b + 0 < a + 0) ? a : b
                ok = (r != "" && r + 0 >= lo + 0 && r + 0 <= hi + 0) }
         printf "    %-26s before=%-9s after=%-9s restored=%-9s %s\n", t, b, a, r, (ok ? "ok" : "MISMATCH")
         if (!ok) bad = 1
       }
       exit bad
     }' "$OUT/counts-before-$STAMP.txt" "$OUT/counts-after-$STAMP.txt" "$OUT/counts-restored-$STAMP.txt" \
  || die "row counts do not match: run again at a quiet time"

echo "7/7 encrypt (AES-256) and verify the encrypted copy"
gpg --batch --yes --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 \
    --symmetric --cipher-algo AES256 -o "$ENC" "$DUMP" 3<<<"$P1" || die "encryption failed"
DEC=$(gpg --batch --quiet --no-symkey-cache --pinentry-mode loopback --passphrase-fd 3 -d "$ENC" 3<<<"$P1" \
      | sha256sum | cut -d' ' -f1)
P1=""
[ "$DEC" = "$(cat "$DUMP.sha256")" ] || { rm -f "$ENC"; die "the encrypted copy does not decrypt to the dump"; }
mv "$DUMP.sha256" "$ENC.plain-sha256"

echo "OK. In $OUT:"
echo "    $(basename "$ENC")   (encrypted backup; copy it to a second place)"
echo "    $(basename "$ENC").plain-sha256, counts-*-$STAMP.txt   (no personal data)"
echo "    The plaintext dump has been deleted."
