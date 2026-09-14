# v1.0.0 發布說明

原始碼：[kuotunyu/enterprise-query-agent](https://github.com/kuotunyu/enterprise-query-agent)。本次發布本機產品原始碼與評估摘要，不包含雲端託管服務。

中文Olist歷史資料工作台，具營收口徑澄清、受控SQL、唯讀MySQL、Decimal數值與查詢證據。提供可重建的合成示例；實際Olist CSV由使用者另行取得，不隨原始碼散布。真模型採GPT-5.6 Luna，另有不呼叫API的mock模式。

一次正式比較完整保留192個結果：40原題的嚴格契約成功數A固定模板30、B直接SQL23、C業務計畫31；24個相依变體另外報告。C比A多一題，不能宣稱一般性優勢；共通支援子集以A更穩定。精確metadata與整題拒絕規則會使部分數字正確／安全處理的答案失敗，報告明確揭露。

發布內容包括原始碼、測試、frozen lockfile、合成開發fixture、README、操作示範、正式／開發結果摘要及來源授權。排除`.local`、實際CSV、金鑰、runtime／bootstrap設定、私人handoff、provider原始traces。完整研究journal與gold保留本機，公开摘要本身不足以獨立重播正式研究。

驗證：[GitHub CI](https://github.com/kuotunyu/enterprise-query-agent/actions/runs/34852517670)離線114 passed、MySQL143 passed／8 skipped／4 deselected；正式harness8項離線測試；真模型桌面與390px手機操作通過；正式資料／事件／候選雜湊核對通過。初次Linux CI的去重測試因wall_seconds差異失敗，僅修正測試比較，不改候選或研究分數。Windows並發帳本測試曾偶發PermissionError，重驗通過但根因未解；正式執行無此中斷。未進行雲端部署。

詳細成績、費用、代表性失敗與研究限制見 [正式報告](../reports/formal-luna.md)。來源、上游MIT授權與資料出處見 [provenance](../provenance/README.md)。
