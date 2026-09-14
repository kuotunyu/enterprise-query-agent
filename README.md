# Enterprise Query Agent

以中文詢問 Olist 電商資料，澄清業務定義後產生受控查詢，回傳可核對的答案、結果表與依據。

**目前狀態：2026-09-14 設計與交接文件完成；產品尚未實作，尚未建立 Git repository／GitHub repository。** 本文件不是產品完成宣告。

## 開發入口

1. [v1 設計](docs/specs/2026-09-14-v1-design.md)
2. [開發驗收案例](docs/acceptance-v1.md)
3. [分階段施工交接](docs/plans/2026-09-14-v1-delivery.md)

v1 目標是有限電商領域的可靠資料查詢，不宣稱支援任意資料库或完整企業 BI。交付包含可操作介面、工程驗證、三方法比較與誠實的失敗報告。正式評估結果不必優於 baseline 才能結案。

## 資料與前作

沿用 [kuotunyu/mysql-ecommerce-analytics](https://github.com/kuotunyu/mysql-ecommerce-analytics) 的 schema／資料清理規則／業務定義，來源版本 `619ad706c35951bd6b811ac56765d6ad097ddbaa`。前作有九份來源 CSV、九張原始對應表及一張衍生地理表；不要把衍生表與分析 views 忽略。

新案使用獨立 MySQL instance／volume。實作時記錄來源檔案與雜湊、保留授權與出處，不直接執行前作的重建命令或變更其資料庫。Olist 資料取自 [原始資料集頁面](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)，原始 CSV 與本機執行資料不作為新 repo 的原始碼附件。

首次實作完成後，將這份 README 改成實際安裝、操作與證據入口；不要把計畫中的功能寫成已交付。
