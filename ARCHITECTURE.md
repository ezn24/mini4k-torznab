# Architecture

## Request flow

```text
Radarr / Sonarr -> Prowlarr -> mini4k-torznab -> FlareSolverr -> Mini4K
```

1. Prowlarr calls `/v2/api` using Torznab parameters.
2. The bridge normalizes movie, TV, season and episode queries.
3. Mini4K pages are fetched through the configured browser backend.
4. Cloud-drive-only rows are discarded.
5. Magnet links are returned directly. Protected `.torrent` files use `/v2/download/<id>` so the bridge can attach the current authenticated session.
6. Results are serialized as Torznab XML.

## Runtime choices

- `compose.yml` + `Dockerfile`: recommended lightweight deployment using an existing FlareSolverr.
- `compose.playwright.yml` + `Dockerfile.playwright`: optional local-browser mode.
- Synology does not use a different application image or Compose model.

## Persistence

`/data` contains session material such as the Mini4K Cookie. It must be mounted from the host and excluded from version control and image build contexts.

## Current code boundary

The HTTP API, Mini4K client, parser and Torznab serializer currently live in `app.py`. This keeps deployment simple but is the next maintenance boundary to split once behavior is covered by parser fixtures and integration tests.

