# Mini4K Torznab

Mini4K Torznab converts Mini4K search results into a Torznab feed for Prowlarr, Radarr, and Sonarr. It returns magnet links and direct `.torrent` files while excluding cloud-drive-only releases.

> **Status: Beta.** Mini4K page layouts, login flows, and Cloudflare rules can change without notice.

## Features

- Movie, TV, season, and episode searches
- 1080p and 2160p category mapping
- Torznab pagination for Prowlarr
- Direct magnet passthrough
- Authenticated proxying for protected `.torrent` files
- Cloud-drive-only result filtering
- Encrypted credentials for automatic account-session renewal
- Tracker-observed seeder and peer counts for magnet releases

## Architecture

```text
Radarr / Sonarr -> Prowlarr -> Mini4K Torznab -> FlareSolverr -> Mini4K
```

Linux Docker and Synology DSM use the same Compose files. The recommended lightweight image uses FlareSolverr as its browser backend.

## Initial configuration

```bash
cp .env.example .env
```

Edit at least these values in `.env`:

- `API_KEY`: a new long random value
- `MEDIA_NETWORK`: the existing Docker network used by Prowlarr
- `DATA_PATH`: the host directory for persistent session data
- `MINI4K_IMAGE`: a published image name, or `mini4k-torznab:local`

Find Prowlarr's network with:

```bash
docker inspect prowlarr --format '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}'
```

## Deployment A: Use an existing FlareSolverr

Set the shared network and the existing container URL:

```dotenv
MEDIA_NETWORK=jellyfin_default
FLARESOLVERR_URL=http://flaresolverr:8191/v1
```

To build the bridge locally:

```bash
docker compose build mini4k-torznab
docker compose up -d mini4k-torznab
```

To use a published image instead:

```dotenv
MINI4K_IMAGE=ghcr.io/OWNER/REPOSITORY:latest
```

```bash
docker compose pull mini4k-torznab
docker compose up -d --no-build mini4k-torznab
```

## Deployment B: No existing FlareSolverr

The `compose.flaresolverr.yml` overlay creates FlareSolverr and attaches it to the same external Docker network as Prowlarr.

Local build:

```bash
docker compose -f compose.yml -f compose.flaresolverr.yml up -d --build
```

Published bridge image:

```bash
docker compose -f compose.yml -f compose.flaresolverr.yml pull
docker compose -f compose.yml -f compose.flaresolverr.yml up -d --no-build
```

The overlay does not publish FlareSolverr on a host port. Other containers reach it at `http://flaresolverr:8191/v1` on `MEDIA_NETWORK`.

## Optional: Run without FlareSolverr

An optional Playwright image is available:

```bash
docker compose -f compose.yml -f compose.playwright.yml up -d --build
```

Playwright mode requires a valid `data/auth_state.json`, or both `data/mini4k-cookie.txt` and `data/mini4k-user-agent.txt`. Automatic credential-based renewal currently belongs to FlareSolverr mode, so Playwright mode is less suitable for unattended servers.

## Automatic login renewal

Build or pull the bridge image, then run:

```bash
docker compose run --rm --no-deps --entrypoint python mini4k-torznab credential_tool.py
```

Copy the generated `MINI4K_CREDENTIALS_KEY` and `MINI4K_CREDENTIALS_TOKEN` into `.env`, then recreate the service:

```bash
docker compose up -d --force-recreate mini4k-torznab
```

Never commit `.env`, cookies, `auth_state.json`, or real credentials.

## Prowlarr configuration

Open **Indexers -> Add Indexer -> Generic Torznab**:

- **Name:** `Mini4K`
- **URL:** `http://mini4k-torznab:8787/v2`
- **API Key:** the value from `.env`
- **Categories:** Movies, Movies/HD, Movies/UHD, TV, TV/HD, TV/UHD
- **Redirect:** disabled

## Verification

```bash
curl http://127.0.0.1:8787/health
curl "http://127.0.0.1:8787/v2/api?t=caps&apikey=YOUR_API_KEY"
```

## Seeder and peer counts

For magnet releases, the bridge extracts the BTIH and queries all advertised HTTP(S) and UDP trackers that support scraping. Tracker populations are not added together because the same client may announce to several trackers. The bridge reports the valid response with the highest seeder count and sets `peers` to seeders plus leechers.

Results are cached for 30 minutes by default. A value of `0` means that no supported tracker returned a count; it does not prove that the torrent has no DHT peers. Direct `.torrent` releases also remain `0` during search because the bridge does not download every torrent file merely to calculate its info-hash.

The relevant environment variables are:

- `TRACKER_SCRAPE_ENABLED=true`
- `TRACKER_SCRAPE_TIMEOUT=3`
- `TRACKER_SCRAPE_CACHE_MINUTES=30`
- `TRACKER_SCRAPE_MAX_RESULTS=50`
- `TRACKER_SCRAPE_WORKERS=12`

## GitHub Container Registry

The included workflow tests the Python code and publishes the lightweight image to `ghcr.io/OWNER/REPOSITORY`. It builds `linux/amd64` and `linux/arm64`, publishes `latest` from the default branch, and creates semantic-version and commit-SHA tags. Pull requests are built but not published.

## Development and local builds

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
python -m py_compile app.py credential_crypto.py credential_tool.py
docker compose config
```

The default Dockerfile builds the lightweight FlareSolverr-backed image. `Dockerfile.playwright` builds the optional browser image.

## Known limitations

- Mini4K and Cloudflare are external dependencies; searches may be slow or temporarily fail.
- CAPTCHA and MFA challenges cannot be handled reliably.
- IMDb- or TVDB-ID-only searches are not supported.
- Seeder values are tracker-observed counts, not an exact global DHT population.
- Empty validation searches use configured test titles. Disable RSS for this indexer if it is not needed.

See [ARCHITECTURE.md](ARCHITECTURE.md), [Synology installation](docs/SYNOLOGY.md), and [security guidance](docs/SECURITY.md).

