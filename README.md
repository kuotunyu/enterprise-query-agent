# Enterprise Query Agent

以中文詢問歷史 Olist 電商資料，先確認業務口徑，再執行受控 SQL，呈現可核對的 BRL 數字、結果表與證據。

本機工作台已接入真實 Olist 歷史資料與 GPT-5.6 Luna，開發驗證與一次正式比較均已完成。40原題嚴格契約成功數為 A固定模板30、B直接SQL23、C業務計畫31；另有24個相依變體。C僅比A多一題，不能推論一般性優勢，共通支援子集以A較穩定。完整結果與評分限制見 [正式報告](reports/formal-luna.md)，工程紀錄見 [工程狀態](docs/status.md)。原始碼已公開於 [kuotunyu/enterprise-query-agent](https://github.com/kuotunyu/enterprise-query-agent)；工作台仍在本機執行。

## 啟動

需要 uv 與已啟動的 Docker Desktop / Docker Engine。命令在本專案根目錄執行。第一次下載 Python 3.12、依賴與 MySQL image 需要網路；mock 查詢不需要 API key。

```powershell
uv sync --frozen --python 3.12
uv run python scripts/configure_local.py  # New pair: add --port 3317 here if 3307 is in use
uv run python scripts/check_mysql_port.py  # First/stopped DB only; skip if this DB already runs
# Continue only if the pre-start check succeeds.
docker compose --env-file .local/bootstrap.env up -d --wait
uv run --env-file .local/bootstrap.env python scripts/bootstrap_data.py
uv run --env-file .local/runtime.env python scripts/serve.py
```

開啟 [本機工作台](http://127.0.0.1:8010)。以上建立可重現的合成環境；已有真資料環境的本機啟動命令見 [工程狀態](docs/status.md)。已載入資料時，日常啟動只需 Compose 與最後一行；不要在查詢時重載資料。

`configure_local.py` 建立隨機密碼，重跑保留既有設定。`.local/bootstrap.env` 供建庫／匯入；Web **只能使用 `.local/runtime.env`**。不要把 bootstrap 密碼放進 Web 環境。

MySQL 使用專案 `eqa_v1`、專用 volume、loopback port **3307**；Web 使用 **8010**。埠占用時報錯，不停止其他服務。資料庫埠可在兩份本機 env 一致修改 `EQA_DB_PORT`；Web 可用 `--port 8011`。原 Olist checkout、資料庫、容器與 volumes 不受本案管理。

### MySQL 啟動前檢查與替代埠

本案與 MovieLens 都預設使用主機 **3307**；專用 volume 不代表連接埠不會衝突。第一次啟動或本案 MySQL 已停止時，先執行：

```powershell
uv run python scripts/check_mysql_port.py
```

檢查成功後才執行上面的 Compose 命令。工具只讀取兩份本機 env 並測試 socket，不連資料庫、不改設定、不停止服務。若本案 MySQL 已經在運作，跳過此檢查，原 Compose 重複啟動方式不變。可用性是當下快照，Compose 仍是最終檢查。

若 3307 已由其他專案使用，新環境可改用 `uv run python scripts/configure_local.py --port 3317`（3317 也需先檢查，EQA 不允許使用 3306）。已有設定則只將 `.local/bootstrap.env` 與 `.local/runtime.env` 的 `EQA_DB_PORT` 一起改成同一個空閒埠，保留其餘內容與密碼；`--port` 不會改寫既有設定。Compose 的 `--env-file` 與 bootstrap/Web 的 `--env-file` 仍各用原本檔案。若使用另一組本機檔案，檢查時以兩次 `--env-file <檔案>` 指定同一組。

Shell 的 `EQA_DB_PORT` 優先於 env 檔，檢查與啟動必須使用相同 shell 設定；建議清除過期覆寫再維護兩份檔案。使用 literal 整數埠，不在埠值內插入其他變數。更改埠只改主機映射，容器內仍是 3306；保留原 Compose project 與 volume，不為解決衝突刪除 volume 或停止其他專案。

## 使用

1. 先確認畫面標示的 mock／openai 模式、資料版本、實際資料日期、coverage policy 與基準日期。
2. 選六個開發示例或輸入中文問題，例如「2018年7月營收多少」。
3. 營收會澄清含運 GMV／不含運商品金額／全部狀態付款。同任務保留已確認口徑；「新任務」重新確認。
4. 查看數字、分母／缺值、結果表與限制；展開證據可查看 SQL、參數、型別、結果列、版本、hash 與用量。

基線手算：7 月 GMV **242.00 BRL**、商品金額 **220.00 BRL**、全部狀態付款 **459.00 BRL**、3 筆 delivered 訂單、AOV **80.67 BRL**、回購／延遲率各 **50%**、最新評論均分 **4**。這是公開合成開發案例，不是模型或真實商業成績。

真資料2018年7月 delivered GMV獨立核對值為 **1,027,807.28 BRL**，訂單6,159筆。真模型操作截圖與兩分鐘展示流程見 [使用與示範](docs/demo.md)。單次示範成功不等於研究成功率。

Mock 是有限 deterministic 規則，不是任意中文理解模型，示範以單月問題為主。明示日區間或跨月而無法解讀時會 unsupported，不默默改成整月；底層 QueryPlan 仍支援精確半開日期區間。不識別或無來源支持的問題會澄清／unsupported。品類與賣家的訂單可能重疊，不能加總分組訂單數。

## 驗證

```powershell
# 無資料庫／provider 的離線測試
uv run pytest -m "not integration and not ui" -q

# 真 MySQL 數值、權限、超時、取消、截斷與身分
$env:EQA_INTEGRATION = "1"
uv run --env-file .local/runtime.env pytest -m "not ui" -q

# 先在另一個終端啟動合成測試 Web（與 8010 真資料工作台分開）：
# uv run --env-file .local/runtime.env python scripts/serve.py --port 8011
# 再執行瀏覽器與 26 題中文 mock；此套件核對合成手算值
uv run playwright install chromium
$env:EQA_WEB_URL = "http://127.0.0.1:8011"
uv run --env-file .local/runtime.env pytest -q
```

缺少 MySQL／Web 的測試明示 skip，不算 pass。CI 分離離線與 MySQL jobs；已在 GitHub Actions 通過離線114項及MySQL143項測試，詳見工程狀態。變體測試只在另建的隔離庫執行，見 [驗證紀錄](reports/engineering.md)。

## 控制與資料

中文問題 → PlannerDecision → QueryPlan 驗證 → compiler → SQLGlot AST policy → SELECT-only MySQL executor → Decimal facts → UI。

- 8 個版本化指標與有限維度／filter。工作台 C 方法沒有 raw SQL escape hatch；比較工具 B 方法產生的 SQL 仍經相同 AST policy 與 SELECT-only executor。所有方法均沒有 shell 或管理員工具。
- 共用 executor 拒絕寫入、多語句、外部 schema、檔案／未知函式、變數、鎖與可覆寫時限的 hints。
- 每任務最多 2 輪澄清、3 次規劃呼叫、3 條業務 SELECT；每 SQL 5 秒、實際處理 60 秒、結果 200 列。等待澄清不計處理時間。
- 聚合先統一正確粒度；Decimal、日期半開區間、來源 DATETIME 不換時區。截斷比較拒絕推算完整分組變化。
- request_id 在服務存活期間去重，revision 阻擋舊答案；記憶體狀態隨重啟清除，不保證 provider 端 exactly-once。

來源：[kuotunyu/mysql-ecommerce-analytics](https://github.com/kuotunyu/mysql-ecommerce-analytics)，commit `619ad706c35951bd6b811ac56765d6ad097ddbaa`。保留 [來源檔案雜湊](provenance/source-manifest.json)、[MIT 授權](provenance/UPSTREAM_LICENSE) 與 [ETL 說明](provenance/README.md)。九張來源表之外包含衍生 geolocation_zip 與兩個 views。

真實 CSV 使用另一個 Compose project、loopback port 與專用 volume，bootstrap/runtime 設定分離。匯入參數為 `--csv-dir <本機CSV目錄> --manifest <本機manifest檔>`；來源唯讀，核對目標 marker，保留 SHA-256／實際 DB row counts／ETL 版本。已載入真資料的庫拒絕被合成資料或另一個 snapshot 覆寫。匯入後 runtime 的 `EQA_DATASET_ID` 必須符合 manifest，再啟動 Web。原始 CSV、secrets、本機身分與未審查 traces 不提交。

## M4 交接

已完成 A 固定模板、B 直接 SQL、C 業務計畫三種 structured-output adapter 與共用離線 runner。`scripts/run_comparison.py --run-id <新名稱> --dataset-manifest <本機manifest>` 固定使用 26 個公開開發問題、預設跑 mock；紀錄寫入 `.local/comparison-runs`，拒绝覆寫既有 run，付費開發另需明確 `--paid`、`EQA_ENABLE_PAID_API=1`、已批准帳本與本機 key。完整命令、真資料身分與驗證結果見 [工程狀態](docs/status.md)。

工作台預設不啟用付費；即使環境有 key 仍走 mock。此專案已核准 `gpt-5.6-luna` 的開發 US$25／正式研究 US$20 獨立上限，兩階段執行完成，含工作台驗證的費用保守上界合計US$0.29124715，精確帳單不可得。新的使用者需自行設定並批准本機額度。已發布 [v1.0.0](https://github.com/kuotunyu/enterprise-query-agent/releases/tag/v1.0.0)，沒有雲端部署。adapter技術細節見 [M4交接](docs/m4-handoff.md)。

本機設定範例（仍停用付費；不包含 key）：

```dotenv
EQA_ENABLE_PAID_API=0
EQA_PROVIDER=openai
EQA_MODEL=gpt-5.6-luna
EQA_COST_STAGE=development
```

付費入口另要求已批准並初始化的 `.local/cost-ledger.json`。兩階段上限一次寫入，不隨重啟重設、不互借；A/B/C 必須注入同一本帳。帳本記錄請求 ID、預留額及 usage，不存 prompt 或 key。每次先持久預留 US$0.5323728（整個模型 context 的長上下文 cache-write 上界＋最大輸出），再送出；有效 usage 依 input 全按 cache-write 費率計算保守上界，並非精確帳單金額。未知 usage／HTTP 失敗保留原預留額。JSON 校驗、獨占鎖與原子置換防止部分寫入／併發超額；崩潰留下鎖時停止並人工核對，不能直接清帳重跑。帳本及鎖需保留，不保證抵抗人為刪改或硬體儲存故障。

僅允許標準 OpenAI endpoint、Luna、low reasoning、4096 最大輸出、零重試與 55 秒內 timeout；文字與 schema 序列化合計限 64 KB。費率包含 cache-write 的保守預留，沒有宣稱 tokenizer 精確估算。runner 的 `--paid` 僅使用 development 帳本與公開開發題，不允許挪用 research 額度。每輪使用新的 run-id；失敗／中斷不覆寫或自動續跑。原始 provider 回覆存私有 provider-traces，發布前必須審查。開發 runner 遇到任務逾時會停止；正式評估將任務／SQL資源逾時保留為失敗並繼續。兩者均保留可辨識的模型格式／SQL錯誤；傳輸、身分、帳本或基礎設施錯誤停止，尚未執行的題目明示保留。
