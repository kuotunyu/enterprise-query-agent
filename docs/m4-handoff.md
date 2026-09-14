# M4 獨立交接

M1–M3 工程與 mock 驗證不等於真實模型能力已證明。M4 不重新開啟已完成工程，也不增加新資料庫、多模型、fine-tuning、多 Agent 或 leaderboard。

## 使用者決策

- **Provider**：目前只實作 OpenAI adapter；是否採用此 provider。更換 provider 是範圍決策。
- **Model**：確切 API model ID／snapshot、參數與 structured-output 支援。沒有預選或默默替換的 model。
- **預算**：開發題費用上限、正式研究總 USD 上限、費率來源與日期、達上限停止規則。尚未批准任何費用。
- **資料**：提供真實 Olist CSV 路徑，匯入獨立 MySQL；確認 manifest、hash、資料期間與清理結果。

批准後才設定 `EQA_ENABLE_PAID_API=1`、`EQA_PROVIDER=openai`、`EQA_MODEL`、`OPENAI_API_KEY`。key 僅本機環境，不提交。成本不可得時標示 unavailable 或依凍結費率估算，不將 unknown 寫成零。

## 順序

1. 用至少 24 個公開開發问题驗證真 adapter／prompt。D01–D20、26 題 mock corpus 與前作十題都屬開發材料，不能轉稱 holdout。
2. 完成 A 固定模板＋slot extraction、B 直接 SQL、C semantic planner 三方法；共用 catalog、資料、模型、澄清需求、SQL executor 與額度。A/B runner 本次未執行。
3. 凍結 code、prompt、catalog、data、model、價格、limits、方法順序、cache policy、Decimal／null／排序規則與中斷恢復政策。
4. 不修改候選的評估 session 編製 40 任務及其中 12 題的兩種變體，gold 封存且不進 prompt；揭露作者／AI 參與重疊限制。
5. 跑一次 protocol，保留所有成功、失敗、token、USD 與時間。不挑成功重跑，不把相依變體當獨立題數。
6. 輸出全體、分題型、共通支援子集與限制。C 不勝過 A/B 仍可完成，不為正增益無限追加調參。

若研究揭露工程 bug，保留原 run，再增加回歸修復；修復後重跑原題不是未見評估。GitHub remote／push／release 仍需獨立發布授權。

## Adapter 依據

依賴由 uv.lock 固定：OpenAI Python SDK 3.13.0、SQLGlot 30.18.0、PyMySQL 1.2.0。adapter 使用 `client.responses.parse(..., text_format=PlannerDecision)` 並停用 SDK 自動重試。

核對官方文件：[OpenAI Python structured outputs](https://github.com/openai/openai-python/blob/main/helpers.md)、[SQLGlot AST](https://github.com/tobymao/sqlglot/blob/main/posts/ast_primer.md)、[MySQL execution limit](https://dev.mysql.com/doc/refman/8.4/en/server-system-variables.html#sysvar_max_execution_time)、[KILL own queries](https://dev.mysql.com/doc/refman/8.4/en/kill.html)、[PyMySQL options](https://pymysql.readthedocs.io/en/latest/modules/connections.html)。沒有真 API 能力數據。
