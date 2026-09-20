# 操作細節：設定檔、連接埠、真資料、真模型與費用帳本

README 只保留啟動與結果；這裡放日常操作時才需要的細節。所有命令在專案根目錄執行。

## 設定檔與帳號分離

`scripts/configure_local.py` 建立隨機密碼，重跑會保留既有設定。它產生兩份本機檔案：

- `.local/bootstrap.env`：建庫／匯入用。
- `.local/runtime.env`：Web 與查詢用，只有 SELECT 權限。

Web **只能使用 `.local/runtime.env`**；`scripts/serve.py` 偵測到 bootstrap 密碼就拒絕啟動。不要把 bootstrap 密碼放進 Web 環境。

MySQL 使用 Compose 專案 `eqa_v1`、專用 volume、loopback port **3307**；Web 使用 **8010**。埠被占用時只報錯，不停止其他服務。資料庫埠可在兩份本機 env 一致修改 `EQA_DB_PORT`；Web 可用 `--port 8011`。原 Olist checkout、資料庫、容器與 volumes 不受本案管理。

已載入資料時，日常啟動只需要 Compose 與 `serve.py` 兩行；不要在查詢時重載資料。

## MySQL 啟動前檢查與替代埠

預設主機埠 **3307** 可能與同一台機器上的其他專案衝突；專用 volume 不代表連接埠不會衝突。第一次啟動或本案 MySQL 已停止時，先執行：

```powershell
uv run python scripts/check_mysql_port.py
```

檢查成功後才執行 Compose 命令。工具只讀取兩份本機 env 並測試 socket，不連資料庫、不改設定、不停止服務。若本案 MySQL 已經在運作，跳過此檢查，原 Compose 重複啟動方式不變。可用性是當下快照，Compose 仍是最終檢查。

若 3307 已由其他專案使用，新環境可改用 `uv run python scripts/configure_local.py --port 3317`（3317 也需先檢查，本案不允許使用 3306）。已有設定則只將 `.local/bootstrap.env` 與 `.local/runtime.env` 的 `EQA_DB_PORT` 一起改成同一個空閒埠，保留其餘內容與密碼；`--port` 不會改寫既有設定。Compose 的 `--env-file` 與 bootstrap／Web 的 `--env-file` 仍各用原本檔案。若使用另一組本機檔案，檢查時以兩次 `--env-file <檔案>` 指定同一組。

Shell 的 `EQA_DB_PORT` 優先於 env 檔，檢查與啟動必須使用相同 shell 設定；建議清除過期覆寫再維護兩份檔案。使用 literal 整數埠，不在埠值內插入其他變數。更改埠只改主機映射，容器內仍是 3306；保留原 Compose project 與 volume，不為解決衝突刪除 volume 或停止其他專案。

## 合成環境的預期數字

README 的啟動命令建立的是可重現的合成資料環境。基線手算值：7 月 GMV **242.00 BRL**、商品金額 **220.00 BRL**、全部狀態付款 **459.00 BRL**、3 筆 delivered 訂單、AOV **80.67 BRL**、回購／延遲率各 **50%**、最新評論均分 **4**。這是公開合成開發案例，用來確認安裝正確，不是模型或真實商業成績。

## 匯入真實 Olist CSV

真實 CSV 使用另一個 Compose project、loopback port 與專用 volume，bootstrap／runtime 設定分離。匯入參數為 `--csv-dir <本機CSV目錄> --manifest <本機manifest檔>`；來源唯讀，核對目標 marker，保留 SHA-256／實際 DB row counts／ETL 版本。已載入真資料的庫拒絕被合成資料或另一個 snapshot 覆寫。匯入後 runtime 的 `EQA_DATASET_ID` 必須符合 manifest，再啟動 Web。原始 CSV、secrets、本機身分與未審查 traces 不提交。

真資料 2018 年 7 月 delivered GMV 的獨立核對值為 **1,027,807.28 BRL**，訂單 6,159 筆；完整核對結果見 [real-data-validation.json](../reports/real-data-validation.json)。操作腳本與日常使用提示見 [兩分鐘操作示範](demo.md)。

## 三種做法的比較工具

A 固定模板、B 直接 SQL、C 業務計畫三種 structured-output adapter 共用同一個離線 runner（`src/enterprise_query/comparison.py`、`comparison_runner.py`）。

```powershell
uv run --env-file .local/runtime.env python scripts/run_comparison.py --run-id <新名稱> --dataset-manifest <本機manifest>
```

固定使用 26 個公開開發問題、預設跑 mock；紀錄寫入 `.local/comparison-runs`，拒絕覆寫既有 run。付費開發另需明確 `--paid`、`EQA_ENABLE_PAID_API=1`、已批准帳本與本機 key。runner 的 `--paid` 僅使用 development 帳本與公開開發題，不允許挪用 research 額度。每輪使用新的 run-id；失敗／中斷不覆寫或自動續跑。開發 runner 遇到任務逾時會停止；正式評估將任務／SQL 資源逾時保留為失敗並繼續。兩者均保留可辨識的模型格式／SQL 錯誤；傳輸、身分、帳本或基礎設施錯誤停止，尚未執行的題目明示保留。

## 接上真模型

工作台預設不啟用付費；即使環境有 key 仍走 mock。要讓工作台改用真模型，需同時滿足：

1. 本機環境設定（key 只放本機，不提交）：

   ```dotenv
   EQA_ENABLE_PAID_API=1
   EQA_PROVIDER=openai
   EQA_MODEL=gpt-5.6-luna
   EQA_COST_STAGE=development
   OPENAI_API_KEY=<本機 key>
   ```

2. 已批准並初始化的 `.local/cost-ledger.json`（`CostLedger.initialize(path, limits)`，既有檔拒絕覆寫）。只有 key 與 enable flag、沒有帳本時不會送出任何請求。

僅允許標準 OpenAI endpoint、`gpt-5.6-luna`、low reasoning、4096 最大輸出、零重試與 55 秒內 timeout；文字與 schema 序列化合計限 64 KB。原始 provider 回覆存私有 provider-traces，發布前必須審查。頁首的 `mock`／`openai` 標籤顯示目前實際模式。

## 費用帳本規格

本專案為 `gpt-5.6-luna` 設定開發 US$25／正式研究 US$20 兩個獨立上限，兩階段已執行完成；含工作台驗證的費用保守上界合計 US$0.29124715，精確帳單不可得。分項見 [正式報告的費用一節](../reports/formal-luna.md#費用)。新的使用者需自行設定並批准本機額度。

- 兩階段上限一次寫入，不隨重啟重設、不互借；A／B／C 必須注入同一本帳。
- 帳本記錄請求 ID、預留額及 usage，不存 prompt 或 key。
- 每次先持久預留 US$0.5323728（整個模型 context 的長上下文 cache-write 上界＋最大輸出），再送出；有效 usage 依 input 全按 cache-write 費率計算保守上界，並非精確帳單金額。費率包含 cache-write 的保守預留，沒有宣稱 tokenizer 精確估算。
- 未知 usage／HTTP 失敗保留原預留額。
- JSON 校驗、獨占鎖與原子置換防止部分寫入／併發超額；崩潰留下鎖時停止並人工核對，不能直接清帳重跑。
- 帳本及鎖需保留，不保證抵抗人為刪改或硬體儲存故障。

## 其他文件

- [兩分鐘操作示範與日常使用](demo.md)
- [v1.0.0 發布說明](release-notes.md)
- [開發驗收案例](acceptance-v1.md)
- [工程狀態](status.md)、[M4 交接](m4-handoff.md)、[v1 設計](specs/2026-09-14-v1-design.md)：開發過程中逐日累積的工作紀錄，保留原樣供查證，不是使用說明。
