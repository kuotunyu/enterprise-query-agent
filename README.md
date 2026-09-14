# Enterprise Query Agent

以中文詢問歷史 Olist 電商資料，先確認業務口徑，再執行受控 SQL，呈現可核對的 BRL 數字、結果表與證據。

本機 M1–M3 工程與 deterministic mock 流程；正式模型能力与三方法研究屬 M4，尚未執行。實際驗證見 [工程狀態](docs/status.md)。沒有已發布的 GitHub repository 或 release。

## 啟動

需要 uv 與已啟動的 Docker Desktop / Docker Engine。命令在本專案根目錄執行。第一次下載 Python 3.12、依賴與 MySQL image 需要網路；mock 查詢不需要 API key。

```powershell
uv sync --frozen --python 3.12
uv run python scripts/configure_local.py
docker compose --env-file .local/bootstrap.env up -d --wait
uv run --env-file .local/bootstrap.env python scripts/bootstrap_data.py
uv run --env-file .local/runtime.env python scripts/serve.py
```

開啟 [本機工作台](http://127.0.0.1:8010)。已載入資料時，日常啟動只需 Compose 與最後一行；bootstrap 會重載本專案合成資料，不要在查詢時重載。

`configure_local.py` 建立隨機密碼，重跑保留既有設定。`.local/bootstrap.env` 供建庫／匯入；Web **只能使用 `.local/runtime.env`**。不要把 bootstrap 密碼放進 Web 環境。

MySQL 使用專案 `eqa_v1`、專用 volume、loopback port **3307**；Web 使用 **8010**。埠占用時報錯，不停止其他服務。資料庫埠可在兩份本機 env 一致修改 `EQA_DB_PORT`；Web 可用 `--port 8011`。原 Olist checkout、資料庫、容器與 volumes 不受本案管理。

## 使用

1. 畫面標示 mock、資料版本、實際資料日期、coverage policy 與基準日期。
2. 選六個開發示例或輸入中文問題，例如「2018年7月營收多少」。
3. 營收會澄清含運 GMV／不含運商品金額／全部狀態付款。同任務保留已確認口徑；「新任務」重新確認。
4. 查看數字、分母／缺值、結果表與限制；展開證據可查看 SQL、參數、型別、結果列、版本、hash 與用量。

基線手算：7 月 GMV **242.00 BRL**、商品金額 **220.00 BRL**、全部狀態付款 **459.00 BRL**、3 筆 delivered 訂單、AOV **80.67 BRL**、回購／延遲率各 **50%**、最新評論均分 **4**。這是公開合成開發案例，不是模型或真實商業成績。

Mock 是有限 deterministic 規則，不是任意中文理解模型，示範以單月問題為主。明示日區間或跨月而無法解讀時會 unsupported，不默默改成整月；底層 QueryPlan 仍支援精確半開日期區間。不識別或無來源支持的問題會澄清／unsupported。品類與賣家的訂單可能重疊，不能加總分組訂單數。

## 驗證

```powershell
# 無資料庫／provider 的離線測試
uv run pytest -m "not integration and not ui" -q

# 真 MySQL 數值、權限、超時、取消、截斷與身分
$env:EQA_INTEGRATION = "1"
uv run --env-file .local/runtime.env pytest -m "not ui" -q

# 另一個終端保持 Web 運行，再執行瀏覽器與 26 題中文 mock
uv run playwright install chromium
$env:EQA_WEB_URL = "http://127.0.0.1:8010"
uv run --env-file .local/runtime.env pytest -q
```

缺少 MySQL／Web 的測試明示 skip，不算 pass。CI 分離離線與 MySQL jobs；本 session 沒有 GitHub Actions 執行成績。變體測試只在另建的隔離庫執行，見 [驗證紀錄](reports/engineering.md)。

## 控制與資料

中文問題 → PlannerDecision → QueryPlan 驗證 → compiler → SQLGlot AST policy → SELECT-only MySQL executor → Decimal facts → UI。

- 8 個版本化指標與有限維度／filter。模型沒有 raw SQL escape hatch、shell 或管理員工具。
- 共用 executor 拒絕寫入、多語句、外部 schema、檔案／未知函式、變數、鎖與可覆寫時限的 hints。
- 每任務最多 2 輪澄清、3 次規劃呼叫、3 條業務 SELECT；每 SQL 5 秒、實際處理 60 秒、結果 200 列。等待澄清不計處理時間。
- 聚合先統一正確粒度；Decimal、日期半開區間、來源 DATETIME 不換時區。截斷比較拒絕推算完整分組變化。
- request_id 在服務存活期間去重，revision 阻擋舊答案；記憶體狀態隨重啟清除，不保證 provider 端 exactly-once。

來源：[kuotunyu/mysql-ecommerce-analytics](https://github.com/kuotunyu/mysql-ecommerce-analytics)，commit `619ad706c35951bd6b811ac56765d6ad097ddbaa`。保留 [來源檔案雜湊](provenance/source-manifest.json)、[MIT 授權](provenance/UPSTREAM_LICENSE) 與 [ETL 說明](provenance/README.md)。九張來源表之外包含衍生 geolocation_zip 與兩個 views。

可選真實 CSV 匯入（本次未執行）：`uv run --env-file .local/bootstrap.env python scripts/bootstrap_data.py --csv-dir <本機CSV目錄>`。來源唯讀，核對目標 marker，保留 SHA-256／row counts／ETL 版本，manifest 寫在 `.local/dataset-manifest.json`。匯入後將 runtime env 的 EQA_DATASET_ID 設成該 manifest 的值，再重啟 Web。原始 CSV、secrets、本機身分與未審查 traces 不提交。

## M4 交接

已有單一 OpenAI structured-output adapter，預設不啟用付費；即使環境有 key 仍走 mock。正式 model 與預算尚未決定，詳見 [M4 交接](docs/m4-handoff.md)。本次沒有保留題、付費 API、部署或 GitHub 發布。
