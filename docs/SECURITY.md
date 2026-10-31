# Security

- `.env`、`data`、Cookie 與 `auth_state.json` 不得提交到版本控制或分享。
- `MINI4K_CREDENTIALS_TOKEN` 是加密帳密，`MINI4K_CREDENTIALS_KEY` 是解密金鑰；兩者同時外洩等同帳密外洩。
- 加密只避免明文出現在專案與資料備份，不防止已控制 NAS 或 Docker 的管理者。
- 日誌不得輸出帳號、密碼、Cookie 或完整 Token。
- 曾經寫入檔案、聊天或截圖的 API Key 與憑證應重新產生；只從目前檔案刪除並不能撤銷舊值。
- 對外發布 `8787` 前應使用防火牆限制來源。Prowlarr 和橋接器在同一 Docker network 時，可移除 Compose 的 `ports`，只保留容器內部存取。

