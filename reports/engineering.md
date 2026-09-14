# 工程驗證紀錄

僅對本機 M1–M3 與 deterministic mock 負責，沒有正式模型、holdout、三方法比較或 GitHub Actions 成績。

## 已觀察並保留的失敗

- 實作前 contracts／parser／compiler／facts／啟動腳本測試失敗，之後補實作並重驗。
- SQLGlot 將 CASE／AND 也列為 Func；初版白名單錯拒合法聚合，加入 AST 回歸後修正。
- 比較期以字面月份配對，month+state 產生錯誤差額；改相對月份配對及精確 evidence refs。
- 先截斷再排序不是全體 top-k；改 SQL 聚合排序後限制。截斷比較明示 rejected。
- `order` 別名導致 MySQL 1064；首次 26 題 mock 為 25/26 預期 terminal status。原始 local run 保留，修正版另建 run，不覆寫。
- AOV 排序 alias 觸發 ONLY_FULL_GROUP_BY；改完整聚合式並增加真 DB 回歸。
- SELECT 尾端分號在 executor 包裝後失敗；加入真 DB 回歸並處理單一終止符。
- 最終審查重現排隊中的規劃在 timeout 後才呼叫 provider，以及舊取消回覆覆寫新任務畫面；各自加入非同步回歸，修復結果另記。
- 真 DB 回歸重現驗身分途中取消後仍送出 SELECT；取消須同時涵蓋前置連線階段與已執行查詢。
- Mock 曾忽略明示日範圍與 MG 州；修正為不支援的日期區間明示 unsupported，州碼保留參數綁定。
- SDK 在 plan=None 的澄清回覆後未保留前一輪口徑；增加雙請求 wire 回歸，下一輪明確帶入同任務使用者回覆。歷史最多三回且沿用 revision／timeout 記憶隔離。

這些是開發失敗，不是正式評估。原始本機 run 留在忽略目錄，公開紀錄僅保留不含憑證／本機路徑的結果。

## 驗證層次

1. 純 Python oracle 核對 D01–D12，不呼叫 compiler 產生 gold。刻意錯誤 fan-out JOIN 必須得到不同金額。
2. 真 MySQL 基線、拆分付款／舊評論變體；SELECT-only、評論文字拒讀、AST、bound filters、Decimal／時間／null／資料版本。
3. server-only 100 ms 上限回傳 MySQL 3024；預設 5 秒 deadline；手動取消後 processlist 無該 query。
4. API request_id 並發去重、revision、澄清／呼叫／SQL 額度、等待不計 active、錯誤狀態；六示例鍵盤、修改意圖、XSS、窄螢幕。
5. 乾淨來源與新隔離 MySQL volume 重新安裝、bootstrap、測試；不用原 Olist 環境。

精確最終通過／skip 數與環境見 [status](../docs/status.md)，不以中途數字冒充最終結果。

最後審查的排隊超時、晚到 usage、舊取消回覆、連線前置取消與 SDK 澄清歷史修正均已通過定向複核。乾淨來源以工程 commit `2520a430a5775d1eb58191536e7739f09851da81` 的 Git archive 重建，60 個 tracked 檔案逐位元與 commit 相符；未帶入原工作環境的虛擬環境、憑證或資料。

## 本機變體驗證

本次另建 `eqa_repro`／loopback 3308，使用專用 volume 與 `.local/repro-db.env`（不提交）。下面命令只適用於已建立該獨立測試環境的本機；測試先核對目的地、每題後還原基線，不操作 3307 工作台資料：

```powershell
$env:EQA_INTEGRATION = "1"
$env:EQA_MUTATION = "1"
uv run --env-file .local/repro-db.env pytest tests/test_data.py tests/test_data_mutations.py -q
```

變體包含 creation／answer／review_id tie-break、缺翻譯／null 品類、日期邊界、全缺值分母、前期零、partial coverage、超過 200 組的全體 top-k 與截斷比較拒絕。這是公開開發變體，不是正式保留題。
