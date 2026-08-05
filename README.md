# unifi-access

Scripts for talking to a UniFi Access controller's developer API.

## Setup

```sh
cp .env.example .env
# fill in UNIFI_ACCESS_API and UNIFI_ACCESS_HOST
```

Get the token from the UniFi Access app: **General → Advanced → API Token**. It grants
full API access, so keep `.env` out of version control (it's gitignored).

## Usage

```sh
./access.sh                       # reachability check + probe of common endpoints
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

## Endpoints probed by default

`users`, `user_groups`, `doors`, `devices`, `visitors`, and
`credentials/nfc_cards/tokens`. Door-related calls (unlock, door status, access policies)
return empty results until reader/hub hardware is adopted by the controller.
