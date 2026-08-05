# unifi-access

Scripts for talking to a UniFi Access controller's developer API.

## Setup

```sh
cp .env.example .env
# fill in UNIFI_ACCESS_API and UNIFI_ACCESS_HOST
./access.sh --check   # verify the tools this needs are installed
```

Get the token from the UniFi Access app: **General → Advanced → API Token**. It grants
full API access, so keep `.env` out of version control (it's gitignored).

## Usage

```sh
./access.sh                       # tool check + reachability + probe of common endpoints
./access.sh --check               # tool check only
./access.sh users                 # GET a single endpoint
./access.sh doors/<id>/unlock PUT # other methods
```

Endpoints are relative to `https://$UNIFI_ACCESS_HOST:$UNIFI_ACCESS_PORT/api/v1/developer`.

| Variable | Default | Notes |
| --- | --- | --- |
| `UNIFI_ACCESS_API` | — | Required. Bearer token. |
| `UNIFI_ACCESS_HOST` | — | Required. Controller hostname or IP. |
| `UNIFI_ACCESS_PORT` | `12445` | Access API port. |

Controllers serve a self-signed cert (`CN=unifi.local`, issued by `UniFi-Access-CA`), so
`access.sh` passes `curl -k`. Any other client needs the same, or the CA pinned.

## Requirements

`curl` is the only hard requirement — `./access.sh --check` reports what's present and
prints the `apt install` line for anything that isn't. Everything else only sharpens the
reachability report and is skipped when absent:

| Tool | Used for | Ubuntu/Debian package |
| --- | --- | --- |
| `curl` | **Required.** Every API call. | `curl` |
| `getent` / `host` / `dig` / `nslookup` | DNS lookup — first one found wins. | `libc-bin` / `bind9-host` / `bind9-dnsutils` |
| `nc` | TCP port probe. Falls back to bash `/dev/tcp`. | `netcat-openbsd` |
| `openssl` | Cert subject/issuer/dates. | `openssl` |

A stock Ubuntu install has `curl`, `getent` and `openssl` but **not** `host` — the script
used to abort there before reaching the API, so no probe may assume a tool exists.

**macOS** works unchanged: the script is Bash 3.2-compatible (so Apple's `/bin/bash`
runs it) and every probe tries alternatives. macOS has no `getent`, so DNS falls to
`host`; it has no coreutils `timeout`, so the port probe uses `nc` — or `gtimeout` from
`brew install coreutils` if you've removed `nc`. Missing-tool hints print `brew` commands
there instead of `apt`.

If the port probe fails, the script stops before the API loop and says so, rather than
spending six 20-second curl timeouts to reach the same conclusion. When the probe can't
run at all it says *that* instead, and continues — a port it never tested is not reported
as unreachable.

## Reaching the API from the internet

The Access API is served by the UniFi console itself (a UDM Pro or similar) on port
`12445`. It is open on the LAN by default and closed from the internet, so remote use
needs a firewall policy. This is the **UniFi Access** application — the door controller —
not the UniFi Network controller, but both are UniFi OS apps served by the same console,
so the firewall mechanics are identical.

### Zone-based firewall (UniFi Network 9.x and later)

Create one policy — **not** a port forward:

| Field | Value |
| --- | --- |
| Source Zone | `External` |
| Source | `IP` → `Specific`, listing the public IPs allowed to call the API |
| **Source Port** | **`Any`** |
| Destination Zone | **`Gateway`** |
| Destination Port | a port object for `12445` |
| Protocol | `TCP` |
| Action | `Allow` |

Two of those are the ones that actually bite:

**Destination Zone must be `Gateway`, not `Internal`.** The API is served *by* the
console, so this is traffic terminating on the gateway rather than being routed through
it to a client. `Internal` is for traffic passing to other hosts and will never match.
On pre-zone-based firmware the same distinction is the rule type **Internet Local**
(traffic to the gateway itself) versus **Internet In** (traffic through it) — the old
`WAN_LOCAL` vs `WAN_IN`.

**Source Port must be `Any`.** This is the easy mistake: `12445` is the *destination*
port. A client's source port is ephemeral and different on every connection, so pinning
the source port to the service port means the rule matches essentially nothing. It fails
silently — the policy looks correct in the UI, and traffic just falls through to the
default deny.

### Security

The token grants full API access, including door unlock where hardware is adopted.
Ubiquiti discourages exposing `12445` at all; if you do, the source IP allow-list above
is the minimum, and callers need a static public IP for it to keep working. Prefer a VPN
into the site over an internet-exposed port where that's an option.

If you allow-list IPv6 sources, note that they only matter once the hostname actually
publishes an `AAAA` record — with an `A` record alone, every client reaches you over IPv4
no matter what its own stack supports.

## Troubleshooting

`./access.sh` distinguishes the failure modes, and the distinction is the diagnosis:

| Symptom | Meaning |
| --- | --- |
| Hostname doesn't resolve | DNS/DDNS problem, before any firewall question |
| Ping answered, TCP silently times out | Packets reach the console; a **firewall policy is dropping** them |
| TCP refused immediately (RST) | Reached the host, nothing listening on that port |
| `HTTP 401`/`403` | Reachable — the token is wrong, expired, or revoked |
| `HTTP 200` with empty `data` | Working; that part of the controller is unprovisioned |

A dropped connection and an unlistened port look nothing alike, so measure before
theorizing: `ping <host>` answering while `nc -z <host> 12445` hangs is close to proof
that the policy — not the network, the ISP, or the service — is what's in the way.

## Endpoints probed by default

`users`, `user_groups`, `doors`, `devices`, `visitors`, and
`credentials/nfc_cards/tokens`. Door-related calls (unlock, door status, access policies)
return empty results until reader/hub hardware is adopted by the controller.
