#!/usr/bin/env bash
# Query the UniFi Access developer API.
#
#   ./access.sh                 # tool check + reachability + probe of common endpoints
#   ./access.sh --check         # tool check only
#   ./access.sh users           # GET a single endpoint
#   ./access.sh doors/<id>/unlock PUT
#
# Reads UNIFI_ACCESS_API and UNIFI_ACCESS_HOST from .env next to this script.
# The controller serves a self-signed UniFi cert, hence curl -k.

set -euo pipefail

# Pure-bash dirname: this must work before the tool check has run.
[[ "$0" == */* ]] && cd "${0%/*}"

have() { command -v "$1" >/dev/null 2>&1; }

# --- tool check -------------------------------------------------------------
# curl is the only hard requirement. The rest sharpen the reachability report
# and are absent on plenty of minimal Ubuntu/Debian images (bind9-host in
# particular is not installed by default), so nothing here may be assumed.
# The DNS and port probes each try several tools because no single one spans
# both platforms: getent and timeout are Linux-only, and macOS ships neither.
# Bash 3.2 compatible throughout, so Apple's /bin/bash runs this unchanged.

OS="$(uname -s 2>/dev/null || echo Linux)"   # guarded: even uname is not assumed

install_hint() { # install_hint <tool>
  if [[ "$OS" == "Darwin" ]]; then
    case "$1" in
      curl|openssl|netcat) echo "brew install $1" ;;
      nc)                  echo "brew install netcat" ;;
      timeout)             echo "brew install coreutils  # provides gtimeout" ;;
      *)                   echo "brew install bind  # host/dig/nslookup" ;;
    esac
    return
  fi
  case "$1" in                                    # Debian/Ubuntu package names;
    curl)             echo "sudo apt install curl" ;;          # Fedora/Arch put
    openssl)          echo "sudo apt install openssl" ;;       # host+dig in
    nc)               echo "sudo apt install netcat-openbsd" ;;# bind-utils.
    host)             echo "sudo apt install bind9-host" ;;
    dig|nslookup)     echo "sudo apt install bind9-dnsutils" ;;
    getent)           echo "sudo apt install libc-bin" ;;
    *)                echo "install $1" ;;
  esac
}

report_tool() { # report_tool <name> <required|optional>
  if have "$1"; then
    printf '  %-9s %s\n' "$1" "$(command -v "$1")"
    return 0
  fi
  printf '  %-9s MISSING (%s) — %s\n' "$1" "$2" "$(install_hint "$1")"
  return 1
}

check_tools() { # 0 = can run, 1 = a hard requirement is missing
  local rc=0
  echo "=== Tools"
  report_tool curl required || rc=1

  local dns=1
  for t in getent host dig nslookup; do
    have "$t" && { report_tool "$t" optional; dns=0; }
  done
  if (( dns )); then
    printf '  %-9s NONE of getent/host/dig/nslookup — %s\n' 'dns' "$(install_hint dig)"
  fi

  # bash's /dev/tcp covers the port probe when nc is absent, so nc is a nicety.
  report_tool nc optional || true
  report_tool openssl optional || true

  (( rc == 0 )) || echo "  -> curl is required; nothing else can run without it."
  return $rc
}

if [[ "${1:-}" == "--check" ]]; then
  check_tools
  exit
fi

# --- config -----------------------------------------------------------------
if [[ -f .env ]]; then
  set -a; . ./.env; set +a
fi
: "${UNIFI_ACCESS_API:?UNIFI_ACCESS_API is not set (expected in .env)}"
: "${UNIFI_ACCESS_HOST:?UNIFI_ACCESS_HOST is not set (expected in .env)}"

HOST="$UNIFI_ACCESS_HOST"
PORT="${UNIFI_ACCESS_PORT:-12445}"
BASE="https://$HOST:$PORT/api/v1/developer"

api() { # api <endpoint> [method]
  curl -sk -m 20 --connect-timeout 8 -X "${2:-GET}" \
    -H "Authorization: Bearer $UNIFI_ACCESS_API" \
    -H 'Accept: application/json' \
    -w '\n[HTTP %{http_code}]\n' \
    "$BASE/$1"
}

if [[ $# -gt 0 ]]; then
  have curl || { echo "curl is required — sudo apt install curl" >&2; exit 1; }
  api "$@"
  exit
fi

# --- reachability -----------------------------------------------------------
# Every probe below degrades to a message instead of a failure: a missing
# diagnostic tool must never abort the API probe, which is the point of the run.

dns_lookup() { # dns_lookup <host>
  if have getent; then
    getent ahostsv4 "$1" | awk -v h="$1" '{print h" -> "$1}' | sort -u
  elif have host; then
    host "$1" 2>&1
  elif have dig; then
    dig +short "$1" 2>&1
  elif have nslookup; then
    nslookup "$1" 2>&1
  else
    echo "no DNS tool available"
  fi
}

tcp_probe() { # tcp_probe <host> <port>; 1 only when the port is PROVABLY shut
  local t=
  # nc first (it knows about timeouts), then bash's /dev/tcp under whichever
  # timeout exists — coreutils' is `timeout` on Linux, `gtimeout` via brew on
  # macOS. A busybox nc without -z fails the first test and lands on /dev/tcp.
  if have nc && nc -z -w 5 "$1" "$2" 2>/dev/null; then
    echo "tcp $1:$2 open"
    return 0
  fi
  for c in timeout gtimeout; do
    have "$c" && { t="$c"; break; }
  done
  if [[ -n "$t" ]] && "$t" 5 bash -c "exec 3<>/dev/tcp/$1/$2" 2>/dev/null; then
    echo "tcp $1:$2 open"
    return 0
  fi
  # Undetermined is not unreachable: with no probe tool, say so and let the API
  # calls be the test rather than blocking the run on a guess.
  if ! have nc && [[ -z "$t" ]]; then
    echo "tcp $1:$2 — no probe tool (nc/timeout), leaving it to curl"
    return 0
  fi
  echo "tcp $1:$2 UNREACHABLE (no route, filtered, or nothing listening)"
  return 1
}

cert_info() { # cert_info <host> <port>
  have openssl || { echo "openssl absent — skipping cert check"; return; }
  echo | openssl s_client -connect "$1:$2" -servername "$1" 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates 2>/dev/null \
    || echo "no TLS handshake"
}

check_tools || exit 1

echo
echo "=== Reachability"
dns_lookup "$HOST" | sed 's/^/  /'
reachable=0
tcp_probe "$HOST" "$PORT" | sed 's/^/  /' || reachable=1
if (( reachable )); then
  echo
  echo "Port $PORT on $HOST is not reachable from here — skipping the API probe"
  echo "(six 20s curl timeouts otherwise). Check the port forward, the controller,"
  echo "and any VPN/firewall between this machine and the site."
  exit 1
fi
cert_info "$HOST" "$PORT" | sed 's/^/  /'

echo
echo "=== API ($BASE)"
for ep in users user_groups doors devices visitors credentials/nfc_cards/tokens; do
  printf -- '--- %s\n' "$ep"
  # Status line first: curl -w appends it AFTER the body, so truncating the body
  # would swallow the status of exactly the largest, most interesting responses.
  resp="$(api "$ep")"
  printf '%s\n' "$resp" | tail -1
  printf '%s' "$resp" | sed '$d' | head -c 800
  echo
done
