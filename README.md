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

## API tokens: scope and expiry

A token is **not** all-or-nothing. When you mint one, it carries a **Validity Period**
(including `Never Expire`) and a permission per category, each set to `None`, `View` or
`Edit`:

`People & Groups` · `Visitor` · `Access Policy` · `Credentials` · `Locations` ·
`Device` · `System Log` · `Webhooks` · `API Server`

That is the complete list — `API Server` is the last one. Note there is **no `Doors`
category**: door operations presumably fall under `Locations` or `Device`, but which one
is unverified here, and it can't be settled on a controller with no doors adopted.

Grant the minimum an integration needs. A token that only reads doors has no business
holding `Edit` on `Credentials` or `People & Groups`, which would let it mint NFC cards
and PIN codes. If you set an expiry, track it — an expired token means the integration
stops working and only the site's own admin can mint a replacement.

## Endpoints

Verified present on a live controller:

`users` · `user_groups` · `doors` · `devices` · `visitors` ·
`credentials/nfc_cards/tokens` · `webhooks/endpoints`

Door-related calls (unlock, door status, access policies) return empty results until
reader/hub hardware is adopted by the controller.

**Permission categories are not URL segments.** `locations`, `system_logs` and
`api_server` are category names, not paths — they 404. An unknown path returns a
distinctive body:

```json
{"code":404,"codeS":"CODE_NOT_FOUND","msg":"The API was not found.","error":"you entered no-man zone"}
```

Worth recognizing, because it means a mistyped path can't be confused with a permission
problem — they look nothing alike. Consult the API reference below for the full endpoint
list; the set above is only what this script probes and has confirmed.

## API reference

[docs/api_reference.md](docs/api_reference.md) is Ubiquiti's full API reference converted
from PDF — 194 pages, 13 chapters, ~300 tables — with `docs/api_reference.html` as a
browsable version with a sidebar TOC. This is where the endpoints the script doesn't
probe are documented.

Regenerate both from the upstream PDF:

```sh
python3 -m venv .venv && .venv/bin/pip install -r tools/requirements.txt
curl -sLO https://assets.identity.ui.com/unifi-access/api_reference.pdf
.venv/bin/python tools/pdf2md.py api_reference.pdf docs/api_reference.md
pandoc docs/api_reference.md -f gfm -t html5 -s --toc --toc-depth=3 \
  --metadata title="UniFi Access API Reference" -o docs/api_reference.html
```

[tools/pdf2md.py](tools/pdf2md.py) recovers structure from font metadata (heading sizes,
`LucidaConsole` for code) and the ruled grid for tables; `pdftotext` and `pandoc` alone
both flatten this PDF badly. Token-frequency diffing against an independent `pdftotext`
extraction confirms nothing is dropped.

Two spots reproduce flaws in the source PDF rather than the conversion: §6.5's NFC
enrollment flowchart is a broken image placeholder upstream (the artwork isn't in the
file), and one `face.detect_distance` cell in §8.2 is scrambled in the PDF's own text
layer.

The generated docs are committed, so the PDF itself is gitignored.
