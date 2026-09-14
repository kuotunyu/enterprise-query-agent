# M4 獨立交接

> 目前施工順序、分工與決策入口統一見 [PROJECT_PLAN.md](../PROJECT_PLAN.md)。本文件保留技術細節，不是另一份待辦清單。

M1–M3 工程與 mock 驗證不等於真實模型能力已證明。M4 不重新開啟已完成工程，也不增加新資料庫、多模型、fine-tuning、多 Agent 或 leaderboard。

## 使用者決策

- **Provider**：已採用 OpenAI adapter；更換 provider 是另一個範圍決策。
- **Model**：使用者已指定 GPT-5.6 Luna，API ID `gpt-5.6-luna`。參數、官方依據與重新估價見 [總計畫第六節](../PROJECT_PLAN.md)。模型不再是待選項；開發 US$25／正式研究 US$20 費用上限已批准。
- **預算**：開發題費用上限、正式研究總 USD 上限、費率來源與日期、達上限停止規則。2026-09-14 使用者已批准上述分項上限，不重複詢問。
- **資料準備由開發 session 處理**：前作 data/raw 已確認有九份 CSV；自行唯讀核對並匯入本案獨立 MySQL，確認 manifest、hash、資料期間與清理結果。不要求使用者再次手動提供已找到的路徑。

批准後才設定 `EQA_ENABLE_PAID_API=1`、`EQA_PROVIDER=openai`、`EQA_MODEL`、`OPENAI_API_KEY`。key 僅本機環境，不提交。成本不可得時標示 unavailable 或依凍結費率估算，不將 unknown 寫成零。

## 順序

1. 用至少 24 個公開開發问题驗證真 adapter／prompt。D01–D20、26 題 mock corpus 與前作十題都屬開發材料，不能轉稱 holdout。
2. 以 A 固定模板＋slot extraction、B 直接 SQL、C semantic planner 三方法的共用比較入口進行真 adapter 開發驗證；共用 catalog、資料、模型、澄清需求、SQL executor 與額度。離線 runner 工程證據以 [工程狀態](status.md) 為準，不等於真模型比較。
3. 凍結 code、prompt、catalog、data、model、價格、limits、方法順序、cache policy、Decimal／null／排序規則與中斷恢復政策。
4. 不修改候選的評估 session 編製 40 任務及其中 12 題的兩種變體，gold 封存且不進 prompt；揭露作者／AI 參與重疊限制。
5. 跑一次 protocol，保留所有成功、失敗、token、USD 與時間。不挑成功重跑，不把相依變體當獨立題數。
6. 輸出全體、分題型、共通支援子集與限制。C 不勝過 A/B 仍可完成，不為正增益無限追加調參。

若研究揭露工程 bug，保留原 run，再增加回歸修復；修復後重跑原題不是未見評估。GitHub remote／push／release 仍需獨立發布授權。

## Adapter 依據

依賴由 uv.lock 固定：OpenAI Python SDK 3.13.0、SQLGlot 30.18.0、PyMySQL 1.2.0。adapter 使用 `client.responses.parse(..., text_format=PlannerDecision)` 並停用 SDK 自動重試。

核對官方文件：[OpenAI Python structured outputs](https://github.com/openai/openai-python/blob/main/helpers.md)、[SQLGlot AST](https://github.com/tobymao/sqlglot/blob/main/posts/ast_primer.md)、[MySQL execution limit](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time)、[KILL own queries](https://dev.mysql.com/doc/refman/8.4/en/kill.html)、[PyMySQL options](https://pymysql.readthedocs.io/en/latest/modules/connections.html)。真 API 開發結果見 [開發摘要](../reports/development-luna.json)；正式評估以 [工程狀態](status.md)所列封存結果為準。
