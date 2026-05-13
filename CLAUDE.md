# Stock Dashboard

即時股市看板：台股、美股、韓股（三星 / SK 海力士）+ 漢他病毒疫情追蹤。

## 部署

- **本地開發**：`./start.sh`（http://localhost:5001）
- **正式環境**：Vercel — https://stock-dashboard-seven-sigma.vercel.app
- **GitHub**：https://github.com/Karol0328/stock-dashboard
- **自動同步**：執行 `./auto_sync.sh` 可監控檔案變更，自動 commit + push 到 GitHub，Vercel 收到後自動重新部署

## 啟動

```bash
./start.sh          # 自動建 venv、裝套件、啟動
# 或手動：
python3 app.py      # http://localhost:5001
```

## 專案結構

```
stock-dashboard/
├── app.py              # Flask 後端，所有 API 邏輯
├── templates/
│   └── index.html      # 單頁前端（原生 JS，無框架）
├── requirements.txt
├── vercel.json         # Vercel 部署設定
├── start.sh            # 一鍵啟動腳本
├── auto_sync.sh        # 檔案變更自動 commit + push
└── CLAUDE.md
```

## API 端點

| 端點 | 說明 | 重整頻率 |
|------|------|---------|
| `GET /api/taiwan` | 加權指數、台積電、台指期、TXO最大痛點 | 30s |
| `GET /api/us` | S&P/NASDAQ 期貨 + 市值前十大 | 30s |
| `GET /api/korea` | 三星電子、SK 海力士 | 30s |
| `GET /api/news` | 四個市場的最新新聞 | 5min |

## 資料來源（全部免費、無需 API Key）

### 股票報價
- **Yahoo Finance** via `yfinance` — 台股 (`*.TW`)、美股、韓股 (`*.KS`)、期貨 (`ES=F`, `NQ=F`)

### 台指期（TX 全日盤）
- **TAIFEX MIS API**：`POST https://mis.taifex.com.tw/futures/api/getQuoteList`
- Body: `{"MarketType":"0","CommodityID":"TX",...}` → 日盤（`-F` 合約）
- Body: `{"MarketType":"1","CommodityID":"TX",...}` → 夜盤（`-M` 合約）
- 回應路徑：`RtData.QuoteList`，取最高成交量的近月合約
- 日盤/夜盤自動選更新的一個（比較 `CDate+CTime`）

### 選擇權最大痛點（TXO Max Pain）
- **TAIFEX 每日選擇權 CSV**：`POST https://www.taifex.com.tw/cht/3/optDataDown`
- Payload: `{down_type:1, commodity_id:TXO, queryStartDate/EndDate: YYYY/MM/DD}`
- 編碼：MS950（Big5 超集）
- 篩選：近月月選 (expiry 無 W/F)、`一般` 交易時段（有 OI 值）、欄位 [11]=未沖銷契約數
- 若今日 OI 尚未發布（`盤後` 段無 OI），自動退到前一個交易日，最多退 3 天
- Max Pain 計算：對每個履約價 K，計算 call writer 損失 + put writer 損失，最小值即 max pain

### 新聞
- **Google News RSS** — 台股、美股、三星、海力士
- **Yahoo Finance news** via `yfinance.Ticker.news` — 三星、海力士補充英文新聞

## 注意事項

- **台股加權指數**收盤時間 09:00–13:30 CST；`^TWII` 在盤後仍回傳當日收盤價
- **台指期全日盤**：日盤 08:45–13:45、夜盤 15:00–翌日 05:00
- **TXO 最大痛點**以近月「月選」OI 計算（非週選），通常與市場現價有一段距離屬正常
- 所有 API 端點使用 `ThreadPoolExecutor` 並行抓取，台股端點最快 ~3s 完成（最大痛點 CSV 約 2s）
- 前端每 30 秒自動重整股價，每 5 分鐘重整新聞

## 常見問題

**台指期顯示 N/A？**
TAIFEX MIS API 在盤中休息時段（13:45–15:00）仍會回傳最後成交價，通常不會 N/A。若真的 N/A 代表 TAIFEX 伺服器無回應。

**最大痛點顯示「no OI data」？**
通常發生在週末或連假，TAIFEX 近 3 個交易日都沒有一般盤 OI 資料。

**美股 top10 順序過時？**
`US_TOP10` 清單在 `app.py` 頂端，直接修改即可。
