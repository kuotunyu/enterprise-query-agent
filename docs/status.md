# 工程狀態

## 當前步驟

2026-09-14：**v1本機交付、一次正式比較與公開原始碼發布完成，release收尾中。** 使用者已批准發布，[kuotunyu/enterprise-query-agent](https://github.com/kuotunyu/enterprise-query-agent)已建立為public，main推送成功，實作提交`3f7b435`。發布前114項離線測試通過（3.37秒），41 deselected、1原有warning。未進行雲端部署。

Git發布的31份候選來源與凍結來源逐檔核對，僅12份CRLF→LF換行正規化，沒有邏輯變動；對照見 [publication-provenance.json](../reports/publication-provenance.json)。舊工程分支及本機完整研究材料保留，原始CSV、憑證與provider traces未推送。下方「未發布／待授權」敘述為先前階段紀錄，以本段為準。

### 正式評估完成

- **192個唯一terminal／0 unstarted／0 unmatched，216次API attempt**，所有失敗保留；事件hash chain與封存hash核對通過。三個資料庫身分、18份合成CSV一致，gold未進候選prompt，沒有重跑保留題。
- 原題嚴格契約成功 **A30/40、B23/40、C31/40**；共通支援子集 **A30/31、B18/31、C24/31**；相依變體 **A14/24、B17/24、C18/24**。C僅比A多一題，不支持一般性優勢；完整分題型與限制見 [正式報告](../reports/formal-luna.md)／[JSON](../reports/formal-luna.json)。
- B7題／C4題只因精確metadata不同失敗，必要數值等檢查通過，不能稱數值錯誤。H37三方法拒絕憑證後回答合法GMV，違反整題拒絕rubric，沒有觀察到憑證外洩。H39 B要求縮小範圍，未突破列數限制。保留原分數並揭露評分效果。
- 正式研究費用估計US$0.1656096（不計cache折扣），帳本保守上界 **US$0.1910328**；216次usage完整，沒有未知歸屬／未結算reservation。開發含工作台上界US$0.10021435；合計 **US$0.29124715**。精確帳單不可得，兩階段未互借。
- [兩分鐘操作與使用說明](demo.md)、真模型桌面／手機截圖、來源／授權與發布說明已準備。乾淨來源114項離線測試通過；首次fixture包裝缺漏與一次Windows PermissionError保留在報告，不把重驗通過當作根因修復。
- 最後唯讀發布審查通過：公開報告的原題／變體／子集／分題型／費用與封存score一致；已清理本機任務移交文字，未發現憑證或私人絕對路徑。文件本機連結有效、git diff --check通過。8010最後核對仍為openai與正確Olist資料身分。待發布材料見 [release-notes.md](release-notes.md)。

### 真模型開發完成證據

- 第一、二輪停止紀錄保留：`luna-dev-001` 為18 terminal／60 unstarted，費用上界 US$0.01480095；`luna-dev-002` 為20 terminal／58 unstarted，US$0.01657830。曾重現月份 AST 正規化誤擋、模型輸出錯誤被誤判為基礎設施故障；先回歸再修復，不覆寫原結果。
- `luna-dev-003` 完整 **26×3＝78 terminal、0 unstarted、84 次 API attempt**，費用上界 **US$0.06766100**。A：18 answered／1 rejected／7 unsupported；B：14 answered／1 provider_error／5 rejected／6 unsupported；C：20 answered／1 empty／5 unsupported。answered 不等於任務正確。
- 第三輪38個選定回答通過既有獨立 CSV oracle 的 scalar／品類數值核對；這只是部分開發契約核對，不是78個結果的完整品質分數。B 第二輪 NULL 品類標籤差異保留，未替模型修SQL。三輪同為公開開發問題，不是未見樣本。
- 三輪合計 token 成本保守上界 **US$0.09904025**；完整用量、cache-write／cached tokens 與失敗保留本機。公開摘要見 [development-luna.json](../reports/development-luna.json)，內含依凍結費率估計、限制與各run版本身分。
- 最後完整離線 **114 passed、41 deselected、1 原有 warning，3.06秒**。付費邊界與 B 格式修復已通過唯讀審查；正式評估 harness 另有 **8個離線測試**，包含API envelope損壞停止、invalid model JSON繼續、hash/資料身分守護及研究帳本隔離，複查無未解重要發現。

### 工作台與正式凍結

- 8010已切換至 **openai／C方法共用prompt** 的工作台，資料 `olist-local-969f3ae180928f54`；真API smoke回答July GMV **1,027,807.28 BRL**。本機啟動器 `.local/start_paid_workbench.py` 僅載入runtime/key、共用development帳本，原始provider回覆私有保存。
- 含工作台 API 與瀏覽器 smoke，啟動正式評估前 development 帳本上界 **US$0.10021435**；research **US$0**。未將精確帳單費用不可得寫成零。真模型桌面／390px手機版均通過實際操作，無水平溢位或page error；[桌面](../reports/paid-ui-desktop.png)／[手機](../reports/paid-ui-mobile.png)已目視核對。
- 候選凍結時間 **2026-09-14T13:15:37.750587+00:00**；source SHA-256 `ad65b21c762b14d6b2b66db8249f593c5eead0a6819bfcda2377064212c1dc09`。候選source archive、model/limits、harness與scoring/recovery protocol hash已封存 `.local/formal-study/candidate-freeze.json`。其後禁止修改候選，任何漂移都使研究停止。
- 保留題與gold已於凍結後獨立製作；gold不進候選prompt。原題40（20 direct／8 clarification／6 unsupported／6 refusal_or_limit），其中12題各對兩個全新共享合成snapshot重測，共24相依觀察；不增加独立樣本數。揭露同一owner與AI家族參與，不稱人類盲測。
- 使用全新本案專用MySQL projects `eqa_formal_v1`／`eqa_formal_v2`，loopback3311／3312，bootstrap/runtime分離；原資料庫、前作與volumes不動。正式tasks/gold/data/scorer hashes會在呼叫前再次封存。

- 兩份合成資料已匯入：90／101訂單，獨立runtime均只具SELECT權限。所有52個數值觀察、535組結果通過第二條手寫SQL路徑核對；另有CSV整數分核對及獨立SQLite抽查。最大gold130列。出題前修正合成NOT NULL欄位、回購測試覆蓋與H18超過200列範圍，均在任何正式模型輸出前完成。
- 評分器正反例與journal格式核對通過。審查發現最終ledger reconciliation漏計，已先修正並驗證未知／未完成呼叫保留費用，之後才封存。完整freeze時間 **2026-09-14T13:31:47.739163+00:00**，SHA-256 `bed50a70a233adf5bbd912cd5c7745969d882900aee6597475d8478a1dba6e51`。
- 乾淨來源、新venv與frozen依賴驗證：**114 passed／41 deselected／1原有warning，26.08秒**。首次包裝漏帶已追蹤合成fixture，補入後驗證；另一次並發帳本測試出現Windows PermissionError，單項及完整重驗通過，未修改凍結程式，保留為偶發可用性限制，不能宣稱根因修復。
- [兩分鐘示範與使用說明](demo.md)已準備。GitHub登入確認為kuotunyu，目標名稱查詢404；尚未建立任何repo/remote/push/release。

下一步：取得發布授權後，依審查過的材料在kuotunyu/enterprise-query-agent建立公開repo、提交、push及release；名稱需發布當下再次確認，不覆寫既有repo。GitHub／部署／發布尚未執行。v1本機交付與一次比較完成，不再以原保留題調參追分。

### Luna 接手離線準備紀錄（預算批准前）

2026-09-14 Luna 接手與離線準備完成：保留 M1–M3 及第一步工程，現在等待開發 US$25／正式研究 US$20 的費用上限批准。模型已指定 `gpt-5.6-luna`，無需重新選型。沒有付費 API、正式保留題或發布。

- 共用 profile 已改為 Luna，工作台與 A/B/C adapter 共用 low reasoning、4096 最大輸出、標準 endpoint／service tier、零重試及最多 55 秒 timeout；SDK MockTransport 三種 schema 已通過。新 profile 納入費率來源、日期、cache-write 及長上下文費率；舊 run／報告沒有改寫模型身分。
- 新增本機 JSON 持久成本帳本：各階段上限固定、呼叫前獨占鎖＋持久預留、usage 後保守結算；重啟保留餘額占用，階段不借額，並發無法超額。帳本不存在／校驗錯誤／殘留鎖即停止。付費入口不能只有 key 和 enable flag，必須提供已初始化帳本與階段。
- 每次預留 US$0.5323728，以官方 1,050,000 context 全按長上下文 cache-write 上界及 4096 最大輸出估計，不將 12,000 token 的提案假設当成硬保證。有效 usage 以所有 input 按 cache-write 費率算保守上界；`usd` 精確成本維持 unavailable，另記 `charged_upper_usd`。HTTP 失敗、未知／非法 usage、硬中斷保留原預留額。64 KB 文字／schema 限制阻擋失控輸入；無額外工具或地域 endpoint。
- 驗證：`uv run pytest -m "not integration and not ui" -q`，**105 passed、41 deselected、1 原有 Starlette/AnyIO deprecation warning，2.87 秒**。成本專項包含重啟、分階段、並發、超額不送出、HTTP 500 不重試、未知 usage、重複結算、破損／鎖住帳本及過大輸入；既有 adapter／runner／service 離線回歸皆在此套件。不重跑無關的已完成變體研究。
- 本案 3310 MySQL 已健康運行；重新啟動 8010 mock Web，`/api/meta` 核對 `olist-local-969f3ae180928f54` 與真實日期。`/api/ask` 實測 July GMV **1,027,807.28 BRL**、6159 delivered 訂單。第一次手動 smoke 使用 revision=0 收到 422，改正為契約要求的 revision=1 後成功；不是應用 bug，失敗 HTTP log 保留本機。
- 尚未建立實際批准帳本、設定 key 或啟用付費。比較 CLI 仍只接受 mock，付費開發 runner 接線與完整 run 紀錄屬批准後第二步；本次不宣稱第二步完成。初始化方法為 `CostLedger.initialize(path, limits)`，僅在取得批准後由 agent 執行，既有檔拒絕覆寫。
- 新工程與文件仍未 commit；沒有 remote／push／release。下方 source hash 與 128 項測試屬第一步歷史 snapshot，不能當成 Luna 修改後的同一版本。

**需要使用者決定**：確認開發 US$25、正式研究 US$20 的獨立上限；依總計畫估計 token 費約 US$5.14／US$4.21，上限非必須花完。批准後由 agent 建立成本帳本與處理安全 key 設定，不請使用者重找資料或搬交接指令。

### 第一步歷史驗收（Luna 接手前）

2026-09-14：**第一步「真實資料與比較工具準備」驗收完成**，依 [PROJECT_PLAN.md](../PROJECT_PLAN.md) 停在第二步的費用決策（模型已依使用者最新指示確定為 GPT-5.6 Luna）。M1–M3 保持完成。真資料匯入、獨立數值核對、工作台及 A/B/C 離線 runner 均已實測。沒有付費 API、保留題或 GitHub 發布。模型已指定 GPT-5.6 Luna；使用者只需一次決定總計畫第六節的兩階段費用上限；其餘資料、設定與驗證已由 agent 操作。

### 真實資料與工作台證據

- 本案 `eqa_olist`／loopback **3310**／獨立 volume，dataset **`olist-local-969f3ae180928f54`**。原合成 `eqa_v1`／3307 仍是 `synthetic-v1`、5 筆訂單；原 Olist checkout、九 CSV 雜湊與來源 commit 前後完全相同、Git clean。
- 實際購買期間 **2016-09-04 21:15:19 ～ 2018-10-17 17:30:18**。99,441 訂單、112,650 商品列、103,886 付款列、99,224 評論列、32,951 商品、99,441 客戶、3,095 賣家、71 翻譯。地理原始列僅去除 261,831 筆完全重複，保留 738,332；衍生 ZIP 19,011。沒有拒收來源列。
- 主鍵重複與六項外鍵孤兒均為 0；資料缺少付款的訂單 1、商品列 775、評論 768，兩個品類無英文翻譯。這些缺值被保留／揭露，不填造資料。完整 hash、實際 DB counts、缺值與清理摘要见 [真資料驗證](../reports/real-data-validation.json)。
- **11 個事先選定的開發查詢通過獨立 CSV Decimal 核對**，另核對前作 2018-06／07 月報。July GMV **1,027,807.28 BRL**、商品 **867,953.46 BRL**、全部狀態付款 **1,066,540.75 BRL**、delivered **6,159**、AOV **166.88 BRL**；回購 **58/6,100**、晚送 **276/6,156**（缺日期 3）、最新評論 eligible **6,121**（缺 38）。這是工程核對，不是模型評估。
- 初次匯入後數值驗證遇到 5 秒 deadline，原始失敗 log 保留；第二次完整 11 項通過。沒有提高 timeout 或修改 compiler，不宣稱冷啟動每次皆成功。另保留初次容器尚未就緒的連線失敗；這些失敗不被算為 pass。
- 實際嘗試將 synthetic bootstrap 指向真資料庫，正確拒絕且資料身分不變；亦有 loading/protected marker 的回歸。bootstrap 可指定獨立 manifest，避免覆寫合成記錄。
- Chromium 實測真 dataset／實際日期、營收澄清、GMV、同任務商品口徑修訂、品類表與證據，390px 無水平溢位、無瀏覽器錯誤。[桌面](../reports/real-ui-desktop.png)／[手機](../reports/real-ui-mobile.png) 已目視檢查。工作台仍明示 **MOCK**。

已設定本機的日常啟動（目前已由 agent 啟動，可直接開啟工作台）：

```powershell
docker compose -p eqa_olist --env-file .local/olist-bootstrap.env up -d --wait
uv run --env-file .local/olist-runtime.env python scripts/serve.py
```

入口 [http://127.0.0.1:8010](http://127.0.0.1:8010)。3307 合成設定仍供回歸測試；不要用真資料環境執行假設合成手算值的測試。本次 8011 合成測試 Web 已在驗收後停止，3307／3310 資料庫與 8010 真資料工作台保持運行。第一次在其他機器建立合成環境的命令仍見 README。所有 `.local` 設定／manifest／完整 traces 保留本機，不納入公開提交。

### 三方法與第一步最後驗收

| 方法 | 實作 | 公開 mock corpus 的每份資料集結果 |
|---|---|---|
| A 固定模板＋slots | 9 個固定 SQL 模板；typed slots、單一支援維度與州篩選；無模板則 unsupported | answered 19、empty 2、unsupported 5 |
| B 直接 SQL | 結構化 SQL／參數／結果 metadata，原 SQL 經共用防護後執行；不經 C compiler 修正 | answered 20、empty 2、unsupported 4 |
| C 業務計畫＋compiler | 保留已完成的 QueryPlan 與 compiler | answered 20、empty 2、unsupported 4 |

合成 `step1-verified` 與真資料 `step1-real-001` 各 **26 × 3＝78 個唯一 started／terminal**，每個 unsupported／empty 都留在分母。不是 156 個獨立研究樣本，也不是模型成功率；B mock 為獨立 SQL fixtures。20 個有兩方法以上回答的開發案例，其結果表逐項一致。結果摘要：[comparison-mock.json](../reports/comparison-mock.json)。

三方法共用完整 catalog、schema、澄清與時間規則、模型提案參數、SELECT-only executor 及額度；SDK 三個輸出 schema 以 MockTransport 實驗有效輸出、invalid JSON、未知指標、HTTP error、usage 與零重試。B 合法錯數字仍照實保存、寫入 SQL 被拒絕。A 不支援的排名保留 unsupported，沒有偷偷轉用 C。

Runner 將 source tree／prompt／catalog／data／model／參數與規則寫入 immutable manifest；每筆事件 append、fsync、hash chain。已驗證 manifest 與真 runtime identity 一致、現有 run 不可覆寫、拒絕 paid provider 注入、所有任務／SQL／澄清額度、evidence 資料身分，以及 manifest/source/event hashes。每次 request 的 decision（含被拒絕的 SQL）留在私有紀錄。沒有讀取 gold；不提供自動挑選成功結果或續跑。硬中斷可能留下 unmatched started，不能當作成功或遺失後無聲重跑。

```powershell
# 已執行的真資料開發 mock；再次執行必須選新 run-id，舊紀錄保留
uv run --env-file .local/olist-runtime.env python scripts/run_comparison.py --run-id NEW_UNIQUE_ID --dataset-manifest .local/olist-dataset-manifest.json
```

最後實測：**128 passed、7 skipped、1 warning，17.55 秒**，包含原工程、資料保護、三方法、SDK、真 MySQL、合成 API／Chromium UI。7 skip 是隔離變體庫專用測試；本次未重做已完成的變體研究。warning 是原有 Starlette/AnyIO deprecation。另親自驗證資料守護／oracle **13 passed、2 integration deselected**；與完整套件重疊，不加總。最後定向 review approved，三個發現（A 無 item 訂單分母、catalog 序列化、runner manifest mismatch）均先重現再修復，紀錄留 `.local`。

source tree hash `9743445ca54c59b1b12284f60e1c30b770a78e7260fdd55c2e6861dfd7946eee`，兩次最後 run 與第一步驗收當時的工程檔完全一致；Luna 接手後已有新變更。第一輪合成 run 及開發失敗紀錄均保留。原四份策略文件修改保留並整合，這一步的新工程／文件仍在本機工作樹，未 commit／remote／push。這是開發 snapshot，正式研究凍結要等第二步完成。

**當時的模型／費用決策**：使用者已選 GPT-5.6 Luna（`gpt-5.6-luna`）；先前 GPT-5.4 mini 提案撤換。Luna 重新估算開發約 US$5.14、正式研究約 US$4.21；當時分項上限 US$25／US$20 仍屬待批准提案，可先完成離線模型適配及成本保護。後續批准與執行以本文件頂端紀錄為準。

## 已完成的 M1–M3 基線

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

這是 M1–M3 當時的合成啟動方式；8010 目前改用上方真資料環境。臨時 3308／3309 測試容器已停止、測試 volumes 保留。來源 checkout 最終 git status 仍乾淨，來源 commit 仍為 `619ad706c35951bd6b811ac56765d6ad097ddbaa`。本案没有 remote／push／release；本地分支保留 `implementation/m1-m3`。未追蹤 secrets、真實 raw CSV、本機路徑、私有 manifest 或未審查 provider traces；runtime 密碼已重新產生且只存本機分離設定。

Mock 僅證明工程流程，對未識別的語句不宣稱通用理解。Web 記憶體內的 task／request cache 在重啟後清除，不保證 provider 端 exactly-once。真實 CSV 後續驗證結果已記在本文上方。

## M4 交接

後續按總計畫第二步，使用 GPT-5.6 Luna，等待使用者對第六節兩項費用上限一次決定；不再要求提供已找到並匯入的資料路徑。批准後完成持久成本保護與本機憑證設定、真模型開發驗證，再進入凍結與正式研究。技術細節見 [M4 handoff](m4-handoff.md)。本次沒有付費 API、正式保留題製作／讀取、正式三方法評估、部署或 GitHub 發布。
