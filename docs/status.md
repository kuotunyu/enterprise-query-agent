# M1–M3 工程驗收完成

日期：2026-09-14。工程 commit：`2520a430a5775d1eb58191536e7739f09851da81`。後續文件與截圖收尾不改應用行為。M1、M2、M3 已完成；M4 正式模型研究與 M5 發布尚未執行。

## 實際驗證

| 驗證 | 結果 | 範圍 |
|---|---|---|
| 主要 MySQL + 完整 API/UI 套件 | **92 passed、7 skipped**，20.51 秒 | 7 項 skip 是只允許 3308 的 mutation tests，不算 pass |
| 獨立 3308 變體套件 | **7 passed**，31.60 秒 | 最新程式；測後還原基線，未修改 3307 |
| 最终 commit 的乾淨來源離線測試 | **63 passed、36 deselected** | 無 DB／provider 網路呼叫 |
| 同一乾淨來源、全新 MySQL volume | **87 passed、8 skipped、4 deselected** | 8 skip＝7 mutation＋1 需 Web URL 的開發流程；4 瀏覽器項未選 |
| 最新服務重啟後 API／UI 重验 | **6 passed**，6.41 秒 | 含 26 個中文流程與六示例鍵盤／XSS／修訂／延遲取消 |
| 最後定向 review | Approved | 審查者另跑 service/SDK、Chromium、真 DB 取消邊界 |

26 題是**公開 mock 開發流程**，最終 26/26 符合預期 terminal status；不是 26 題未見評估，也不是模型正確率。第一輪 25/26 的失敗仍保留，修正版使用新的 run 檔。六示例在 [mock-demo.json](../reports/mock-demo.json)，畫面在 [桌面截圖](../reports/ui-desktop.png)／[手機截圖](../reports/ui-mobile.png)。

上述套件有重疊，不加總成獨立研究樣本。測試出現 1 個上游 Starlette/AnyIO deprecation warning；沒有測試失敗，不隱藏此警告。原始本機 logs、manifest、review、失敗紀錄存 `.local/` 且未提交。可公開的失敗與修復摘要見 [engineering](../reports/engineering.md)。

## 各里程碑

**M1 完成**：只在本 checkout 初始化 Git，repo-local 作者 kuotunyu；來源 SHA／檔案 hash／MIT attribution 保留。獨立 MySQL 8.4.11、九張來源表＋geolocation_zip＋兩 views、合成 fixture、dataset/schema/ETL 身分、SELECT-only runtime、bootstrap 匯入工具已交付。D01–D12 以固定手算與獨立純 Python oracle 核對；錯誤 fan-out JOIN 被測試抓出。拆分付款與舊評論變體保持正確總額／最新評分。

**M2 完成**：受限 QueryPlan/catalog、參數化 compiler、SQLGlot AST、真 DB 權限、單一 SELECT／合法 CTE、函式／schema／欄位限制、Decimal facts／精確 evidence references。D13–D20 及 E01–E06/E11/E12 的工程契約有對應回歸。真 server-only 100 ms 上限回傳 3024；預設 5 秒 deadline 與取消後無殘留 query 已實測。取消包含 pending connection、身份確認、dispatch 與正在執行階段；KILL 後有限確認自有連線停止。結果 200 列、全體排序後取 top-k，截斷比較明示拒絕。

**M3 完成**：中文單頁、澄清／新任務／修改意圖、答案／分母／限制／結果表／SQL 證據、空資料與錯誤狀態、鍵盤操作。每任務兩輪澄清、三次規劃、三條業務 SQL、60 秒 active time，等待使用者不計時。request_id 去重、revision、取消、provider 排隊超時與晚到 usage 有回歸。OpenAI SDK 透過離線 MockTransport 驗證 structured output、invalid JSON、未知 metric、refusal、跨輪澄清歷史；沒有真 API 呼叫。CI 已配置離線與 MySQL jobs，但沒有 GitHub Actions 遠端執行成績。

## 啟動與工程邊界

[README](../README.md) 有完整乾淨啟動命令。現有本機日常啟動：

```powershell
docker compose --env-file .local/bootstrap.env up -d --wait
uv run --env-file .local/runtime.env python scripts/serve.py
```

入口 [http://127.0.0.1:8010](http://127.0.0.1:8010)，資料庫 loopback 3307、dataset `synthetic-v1`，實際購買日期 2018-06-10 至 2018-07-22。金額單位 BRL；來源完整月 policy 是 2017-01 至 2018-08，不能把此政策當成合成或真實資料完整性證明。

目前 3307 與 8010 保持可用。臨時 3308／3309 測試容器已停止、測試 volumes 保留。來源 checkout 最終 git status 仍乾淨，來源 commit 仍為 `619ad706c35951bd6b811ac56765d6ad097ddbaa`。本案没有 remote／push／release；本地分支保留 `implementation/m1-m3`。未追蹤 secrets、真實 raw CSV、本機路徑、私有 manifest 或未審查 provider traces；runtime 密碼已重新產生且只存本機分離設定。

Mock 僅證明工程流程，對未識別的語句不宣稱通用理解。Web 記憶體內的 task／request cache 在重啟後清除，不保證 provider 端 exactly-once。真實 CSV 匯入程式只以合成 CSV 驗證，未匯入真 Olist CSV。

## M4 交接

待使用者決定 provider、確切 model ID／snapshot、開發／正式研究 USD 上限與價格來源，並提供真資料匯入路徑。再做模型開發驗證、三方法實作／凍結、獨立保留題編製、一次完整研究。詳見 [M4 handoff](m4-handoff.md)。本次沒有付費 API、正式保留題製作／讀取、正式三方法評估、部署或 GitHub 發布。到此停止新增功能。
