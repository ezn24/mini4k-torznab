# Mini4K Torznab

Mini4K Torznab 可將 Mini4K 的搜尋結果轉換為 Torznab 資料來源，供 Prowlarr、Radarr 和 Sonarr 使用。支援磁力連結及直接下載的 `.torrent` 種子檔案，並自動排除僅提供網盤下載的資源。

> **目前狀態：Beta 測試版。** Mini4K 的網頁版面、登入流程及 Cloudflare 規則可能隨時變更。

## 功能特色

- 支援電影、影集、整季及單集搜尋
- 支援 1080p 與 2160p 畫質分類對應
- 支援 Prowlarr 所需的 Torznab 分頁功能
- 直接傳遞磁力連結
- 透過已驗證身分的代理服務下載受保護的 `.torrent` 檔案
- 自動過濾僅提供網盤下載的搜尋結果
- 使用加密帳號憑證，自動更新登入工作階段
- 透過 Tracker 查詢磁力連結資源的做種者（Seeder）及節點（Peer）數量

## 系統架構

```text
Radarr / Sonarr -> Prowlarr -> Mini4K Torznab -> FlareSolverr -> Mini4K
```

Linux Docker 與群暉 Synology DSM 使用相同的 Docker Compose 設定檔。

建議使用輕量化映像檔，並以 FlareSolverr 作為瀏覽器後端。

## 初始設定

執行以下指令，建立環境變數設定檔：

```bash
cp .env.example .env
```

至少需要編輯 `.env` 中的以下設定：

- `API_KEY`：產生一組新的高強度隨機 API 金鑰
- `MEDIA_NETWORK`：Prowlarr 目前使用的 Docker 網路名稱
- `DATA_PATH`：主機上用於永久保存工作階段資料的目錄
- `MINI4K_IMAGE`：設定為 `ghcr.io/ezn24/mini4k-torznab:latest`，或使用本機映像檔 `mini4k-torznab:local`

可使用以下指令查詢 Prowlarr 所使用的 Docker 網路：

```bash
docker inspect prowlarr --format '{{range $k,$v := .NetworkSettings.Networks}}{{println $k}}{{end}}'
```

## 部署方式 A：使用現有的 FlareSolverr

設定共用的 Docker 網路，以及現有 FlareSolverr 容器的 URL：

```dotenv
MEDIA_NETWORK=jellyfin_default
FLARESOLVERR_URL=http://flaresolverr:8191/v1
```

若要在本機建置 Mini4K Torznab：

```bash
docker compose build mini4k-torznab
docker compose up -d mini4k-torznab
```

若要改用已發布的 Docker 映像檔：

```dotenv
MINI4K_IMAGE=ghcr.io/ezn24/mini4k-torznab:latest
```

此專案的 GitHub 儲存庫及其套件預設為私人存取。

下載映像檔之前，需要使用具有 `read:packages` 權限的 GitHub 個人存取權杖（Personal Access Token，PAT），讓 NAS 通過 GitHub Container Registry 驗證：

```bash
echo "YOUR_GITHUB_TOKEN" | docker login ghcr.io -u ezn24 --password-stdin
```

接著執行：

```bash
docker compose pull mini4k-torznab
docker compose up -d --no-build mini4k-torznab
```

## 部署方式 B：尚未安裝 FlareSolverr

`compose.flaresolverr.yml` 是一個額外的 Docker Compose 設定檔。

它會自動建立 FlareSolverr 容器，並將其連接至與 Prowlarr 相同的外部 Docker 網路。

**使用本機建置：**

```bash
docker compose -f compose.yml -f compose.flaresolverr.yml up -d --build
```

**使用已發布的 Mini4K Torznab 映像檔：**

```bash
docker compose -f compose.yml -f compose.flaresolverr.yml pull
docker compose -f compose.yml -f compose.flaresolverr.yml up -d --no-build
```

此額外設定檔不會將 FlareSolverr 的連接埠公開至主機。

其他位於 `MEDIA_NETWORK` 網路內的容器，可透過以下位址存取：

```text
http://flaresolverr:8191/v1
```

## 選用：不使用 FlareSolverr

此專案另外提供可選用的 Playwright 映像檔：

```bash
docker compose -f compose.yml -f compose.playwright.yml up -d --build
```

Playwright 模式需要以下其中一種驗證資料：

- 有效的 `data/auth_state.json`
- 同時具有 `data/mini4k-cookie.txt` 和 `data/mini4k-user-agent.txt`

目前，透過帳號密碼自動更新登入工作階段的功能僅支援 FlareSolverr 模式。

因此，Playwright 模式較不適合需要長期無人值守運作的伺服器。

## 自動更新登入狀態

首先建置或下載 Mini4K Torznab 映像檔，接著執行：

```bash
docker compose run --rm --no-deps --entrypoint python mini4k-torznab credential_tool.py
```

將產生的以下兩個值複製至 `.env`：

- `MINI4K_CREDENTIALS_KEY`
- `MINI4K_CREDENTIALS_TOKEN`

接著重新建立容器，使新的設定生效：

```bash
docker compose up -d --force-recreate mini4k-torznab
```

**安全注意事項：**

絕對不要將 `.env`、Cookie、`auth_state.json` 或任何真實帳號憑證提交至 Git 儲存庫。

## Prowlarr 設定

開啟 Prowlarr，依序前往：

**Indexers → Add Indexer → Generic Torznab**

填入以下設定：

| 設定項目 | 設定值 |
|---|---|
| Name（名稱） | `Mini4K` |
| URL（網址） | `http://mini4k-torznab:8787/v2` |
| API Key | `.env` 中設定的 `API_KEY` |
| Categories（分類） | Movies、Movies/HD、Movies/UHD、TV、TV/HD、TV/UHD |
| Redirect（重新導向） | 停用 |

## 驗證服務是否正常運作

使用以下指令檢查服務健康狀態：

```bash
curl http://127.0.0.1:8787/health
```

檢查 Torznab API 是否正常回應：

```bash
curl "http://127.0.0.1:8787/v2/api?t=caps&apikey=YOUR_API_KEY"
```

請將 `YOUR_API_KEY` 替換為 `.env` 中設定的 API 金鑰。

## 做種者與節點數量

對於磁力連結資源，Mini4K Torznab 會擷取 BTIH（BitTorrent Info Hash），並查詢所有已宣告且支援 Scrape 查詢的 HTTP(S) 與 UDP Tracker。

由於同一個 BitTorrent 用戶端可能同時向多個 Tracker 回報，因此系統不會直接加總不同 Tracker 回傳的節點數量，避免重複計算。

系統會：

1. 查詢支援 Scrape 的 Tracker。
2. 收集各 Tracker 回傳的做種者與下載者數量。
3. 選擇做種者數量最高的有效回應。
4. 將該回應的做種者數量作為 `seeders`。
5. 將做種者與下載者數量相加，作為 `peers`。

搜尋結果預設快取 30 分鐘。

**關於數值為 `0` 的情況：**

當做種者數量顯示為 `0`，代表沒有任何受支援的 Tracker 成功回傳數量資訊。

這不代表該種子在 DHT 網路中完全沒有節點。

另外，對於直接提供 `.torrent` 檔案的資源，搜尋階段的數量也會維持為 `0`。

原因是系統不會為了計算 Info Hash，而在每次搜尋時下載所有 `.torrent` 檔案。

### 相關環境變數

```dotenv
TRACKER_SCRAPE_ENABLED=true
TRACKER_SCRAPE_TIMEOUT=3
TRACKER_SCRAPE_CACHE_MINUTES=30
TRACKER_SCRAPE_MAX_RESULTS=50
TRACKER_SCRAPE_WORKERS=12
```

| 環境變數 | 說明 |
|---|---|
| `TRACKER_SCRAPE_ENABLED` | 是否啟用 Tracker Scrape 查詢 |
| `TRACKER_SCRAPE_TIMEOUT` | Tracker 查詢逾時秒數，預設 3 秒 |
| `TRACKER_SCRAPE_CACHE_MINUTES` | 查詢結果快取時間，預設 30 分鐘 |
| `TRACKER_SCRAPE_MAX_RESULTS` | 執行 Tracker 查詢的搜尋結果數量上限，預設 50 |
| `TRACKER_SCRAPE_WORKERS` | 同時執行 Tracker 查詢的工作執行緒數量，預設 12 |

## GitHub Container Registry

專案內建的 GitHub Actions 工作流程會自動測試 Python 程式碼，並將輕量化 Docker 映像檔發布至：

```text
ghcr.io/ezn24/mini4k-torznab
```

此工作流程具有以下功能：

- 支援建置 `linux/amd64` 與 `linux/arm64` 架構
- 從預設分支發布 `latest` 標籤
- 自動產生語意化版本（Semantic Versioning）標籤
- 自動產生 Commit SHA 版本標籤
- Pull Request 僅執行建置，不會發布映像檔

## 開發與本機建置

安裝 Python 相依套件：

```bash
python -m pip install -r requirements.txt
```

執行單元測試：

```bash
python -m unittest discover -s tests -v
```

檢查 Python 程式碼是否存在語法錯誤：

```bash
python -m py_compile app.py credential_crypto.py credential_tool.py
```

驗證 Docker Compose 設定：

```bash
docker compose config
```

預設的 `Dockerfile` 會建置使用 FlareSolverr 作為後端的輕量化映像檔。

`Dockerfile.playwright` 則用於建置選用的 Playwright 瀏覽器映像檔。

## 已知限制

- **依賴外部服務：** Mini4K 與 Cloudflare 均屬於外部依賴項目，因此搜尋可能較慢，甚至暫時失敗。
- **CAPTCHA 與 MFA：** 無法可靠地自動處理驗證碼或多因素驗證挑戰。
- **不支援純 ID 搜尋：** 無法僅使用 IMDb ID 或 TVDB ID 執行搜尋。
- **做種數量並非全網精確值：** 顯示的是 Tracker 觀測到的做種者數量，而非整個 DHT 網路的實際節點總數。
- **空白驗證搜尋：** 當驗證搜尋沒有提供關鍵字時，系統會使用預先設定的測試標題。如果不需要此索引器的 RSS 功能，建議將其停用。

## 相關文件

如需了解更多資訊，請參閱：

- [ARCHITECTURE.md](ARCHITECTURE.md)：系統架構與運作原理
- [群暉 Synology 安裝指南](docs/SYNOLOGY.md)：Synology NAS 部署教學
- [安全性指南](docs/SECURITY.md)：帳號憑證、API 金鑰與服務安全設定
