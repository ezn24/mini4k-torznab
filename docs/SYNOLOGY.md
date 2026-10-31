# Synology DSM 7.2

群暉與一般 Docker 使用同一份 `compose.yml`，不再維護 Synology 專用 Compose 或 Dockerfile。

## 1. 放置專案

例如建立 `/volume2/Jellyfin/mini4k`，把專案檔案放入該目錄，並由 `.env.example` 複製出 `.env`。

## 2. 設定 `.env`

你的環境可使用：

```dotenv
MEDIA_NETWORK=jellyfin_default
DATA_PATH=/volume2/Jellyfin/mini4k/data
PUBLISHED_PORT=8787
```

`MEDIA_NETWORK` 必須是 Prowlarr 與既有 FlareSolverr 實際共用的 network。可從 SSH 查詢：

```bash
docker inspect jellyfin-prowlarr --format '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}'
docker inspect flaresolverr --format '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}'
```

若 FlareSolverr 的容器名稱不是 `flaresolverr`，同步修改 `.env` 的 `FLARESOLVERR_URL`。

## 3. 建立與更新

```bash
cd /volume2/Jellyfin/mini4k
docker compose build mini4k-torznab
docker compose up -d mini4k-torznab
```

更新程式後：

```bash
docker compose up -d --build --force-recreate mini4k-torznab
```

不需要 `-f docker-compose.synology.yml`，也不需要在 service 的 `networks` 內加入 `external`；`external` 只出現在 `compose.yml` 最下方的頂層 network 定義。

## 4. 驗證

```bash
curl http://127.0.0.1:8787/health
docker exec jellyfin-prowlarr wget -T 10 -O- 'http://mini4k-torznab:8787/v2/api?t=caps'
```

第一個測試服務本身，第二個測試 Prowlarr 到橋接器的 Docker network。

