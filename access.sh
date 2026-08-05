#!/usr/bin/env bash
# Query the UniFi Access developer API.
#
#   ./access.sh                 # reachability + summary probe of common endpoints
#   ./access.sh users           # GET a single endpoint
#   ./access.sh doors/<id>/unlock PUT
#
# Reads UNIFI_ACCESS_API and UNIFI_ACCESS_HOST from .env next to this script.
# The controller serves a self-signed UniFi cert, hence curl -k.

set -euo pipefail

cd "$(dirname "$0")"
if [[ -f .env ]]; then
  set -a; . ./.env; set +a
fi
: "${UNIFI_ACCESS_API:?UNIFI_ACCESS_API is not set (expected in .env)}"
: "${UNIFI_ACCESS_HOST:?UNIFI_ACCESS_HOST is not set (expected in .env)}"

HOST="$UNIFI_ACCESS_HOST"
PORT="${UNIFI_ACCESS_PORT:-12445}"
BASE="https://$HOST:$PORT/api/v1/developer"

api() { # api <endpoint> [method]
  curl -sk -m 20 -X "${2:-GET}" \
    -H "Authorization: Bearer $UNIFI_ACCESS_API" \
    -H 'Accept: application/json' \
    -w '\n[HTTP %{http_code}]\n' \
    "$BASE/$1"
}

if [[ $# -gt 0 ]]; then
  api "$@"
  exit
fi

echo "=== Reachability"
host "$HOST" 2>&1 | sed 's/^/  /'
nc -zv -w 5 "$HOST" "$PORT" 2>&1 | sed 's/^/  /'
echo | openssl s_client -connect "$HOST:$PORT" -servername "$HOST" 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates 2>&1 | sed 's/^/  /'

echo
echo "=== API ($BASE)"
for ep in users user_groups doors devices visitors credentials/nfc_cards/tokens; do
  printf -- '--- %s\n' "$ep"
  api "$ep" | head -c 800
  echo
done
