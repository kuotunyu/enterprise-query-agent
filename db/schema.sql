CREATE TABLE product_category_name_translation (
    product_category_name         VARCHAR(64) NOT NULL COMMENT '葡文類別名（實測最長 46 字元）',
    product_category_name_english VARCHAR(64) NOT NULL COMMENT '英文譯名（實測最長 39 字元）',
    PRIMARY KEY (product_category_name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='商品類別葡→英對照（71 列原樣，缺譯不補）';

CREATE TABLE customers (
    customer_id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    customer_unique_id       CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '自然人識別，跨訂單重複',
    customer_zip_code_prefix CHAR(5)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'CEP 前 5 碼，保留前導零',
    customer_city            VARCHAR(64) NOT NULL COMMENT '實測最長 32 字元',
    customer_state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '巴西州代碼（實測 27 種）',
    PRIMARY KEY (customer_id),
    KEY idx_customers_unique_id (customer_unique_id) COMMENT 'RFM/cohort：折疊自然人',
    KEY idx_customers_state     (customer_state)     COMMENT '州級營收/物流分析',
    KEY idx_customers_zip       (customer_zip_code_prefix) COMMENT 'JOIN geolocation_zip'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='客戶（訂單維度）；一自然人多 customer_id';

CREATE TABLE sellers (
    seller_id              CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    seller_zip_code_prefix CHAR(5)  CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT 'CEP 前 5 碼，保留前導零',
    seller_city            VARCHAR(64) NOT NULL COMMENT '實測最長 40 字元（含髒值，ETL 僅 strip 不改寫）',
    seller_state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (seller_id),
    KEY idx_sellers_zip (seller_zip_code_prefix) COMMENT 'JOIN geolocation_zip'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='賣家';

CREATE TABLE geolocation (
    geolocation_id              INT UNSIGNED NOT NULL AUTO_INCREMENT COMMENT '代理鍵（CSV 無自然主鍵）',
    geolocation_zip_code_prefix CHAR(5) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '保留前導零',
    geolocation_lat             DECIMAL(10,8) NOT NULL COMMENT '8 位小數 ≈ mm 級精度',
    geolocation_lng             DECIMAL(11,8) NOT NULL,
    geolocation_city            VARCHAR(64) NOT NULL COMMENT '實測最長 38 字元',
    geolocation_state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    PRIMARY KEY (geolocation_id),
    KEY idx_geolocation_zip (geolocation_zip_code_prefix)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='地理座標點（五欄完全重複列已去重：1,000,163→738,332）';

CREATE TABLE geolocation_zip (
    zip_code_prefix CHAR(5) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    lat             DECIMAL(10,8) NOT NULL COMMENT 'bbox 內座標點的中位數',
    lng             DECIMAL(11,8) NOT NULL COMMENT 'bbox 內座標點的中位數',
    city            VARCHAR(64) NOT NULL COMMENT '眾數',
    state           CHAR(2) CHARACTER SET ascii COLLATE ascii_bin NOT NULL COMMENT '眾數',
    n_points        INT UNSIGNED NOT NULL COMMENT '聚合使用的原始點數（bbox 過濾後）',
    PRIMARY KEY (zip_code_prefix)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='衍生：每 zip 一列的地理彙總（分析用介面）';

CREATE TABLE products (
    product_id                 CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    product_category_name      VARCHAR(64) NULL COMMENT '610 列 NULL；不建 FK → translation（2 類缺譯），LEFT JOIN + COALESCE',
    product_name_length        SMALLINT UNSIGNED NULL COMMENT 'CSV 原始欄名: product_name_lenght（拼字錯誤已改正）；實測 max 76',
    product_description_length SMALLINT UNSIGNED NULL COMMENT 'CSV 原始欄名: product_description_lenght；實測 max 3,992',
    product_photos_qty         TINYINT UNSIGNED NULL COMMENT '實測 max 20',
    product_weight_g           INT UNSIGNED NULL COMMENT '實測 max 40,425',
    product_length_cm          SMALLINT UNSIGNED NULL,
    product_height_cm          SMALLINT UNSIGNED NULL,
    product_width_cm           SMALLINT UNSIGNED NULL,
    PRIMARY KEY (product_id),
    KEY idx_products_category (product_category_name) COMMENT '類別分析 JOIN/GROUP BY 主路徑'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='商品';

CREATE TABLE orders (
    order_id                      CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    customer_id                   CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    order_status                  VARCHAR(20) NOT NULL COMMENT '實測 8 種值（最長 unavailable=11）；不用 ENUM 避免綁死值域',
    order_purchase_timestamp      DATETIME NOT NULL COMMENT '實測範圍 2016-09-04 ~ 2018-10-17',
    order_approved_at             DATETIME NULL,
    order_delivered_carrier_date  DATETIME NULL,
    order_delivered_customer_date DATETIME NULL,
    order_estimated_delivery_date DATETIME NOT NULL,
    PRIMARY KEY (order_id),
    KEY idx_orders_status_purchase (order_status, order_purchase_timestamp)
        COMMENT '最常見查詢模式：WHERE status=delivered AND 期間範圍',
    KEY idx_orders_purchase  (order_purchase_timestamp) COMMENT '不分 status 的時間軸掃描（趨勢/cohort）',
    KEY idx_orders_delivered (order_delivered_customer_date) COMMENT '物流時效：實際 vs 預estimated',
    CONSTRAINT fk_orders_customer FOREIGN KEY (customer_id) REFERENCES customers (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='訂單（FK→customers 實測孤兒=0）';

CREATE TABLE order_items (
    order_id            CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    order_item_id       TINYINT UNSIGNED NOT NULL COMMENT '訂單內行號（實測 max 21）',
    product_id          CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    seller_id           CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    shipping_limit_date DATETIME NOT NULL,
    price               DECIMAL(10,2) NOT NULL COMMENT '實測 max 6,735.00',
    freight_value       DECIMAL(10,2) NOT NULL COMMENT '實測 max 409.68',
    PRIMARY KEY (order_id, order_item_id),
    KEY idx_items_product_price (product_id, price) COMMENT '覆蓋索引：商品/類別營收聚合免回表；同時供 FK 使用',
    CONSTRAINT fk_items_order   FOREIGN KEY (order_id)   REFERENCES orders (order_id),
    CONSTRAINT fk_items_product FOREIGN KEY (product_id) REFERENCES products (product_id),
    CONSTRAINT fk_items_seller  FOREIGN KEY (seller_id)  REFERENCES sellers (seller_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='訂單明細（GMV 口徑：SUM(price+freight_value)）';

CREATE TABLE order_payments (
    order_id             CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    payment_sequential   TINYINT UNSIGNED NOT NULL COMMENT '同單付款序號',
    payment_type         VARCHAR(20) NOT NULL COMMENT '實測 5 種（含 not_defined×3）',
    payment_installments TINYINT UNSIGNED NOT NULL COMMENT '實測 0..24（0 為原始資料）',
    payment_value        DECIMAL(10,2) NOT NULL COMMENT '實測 max 13,664.08',
    PRIMARY KEY (order_id, payment_sequential),
    CONSTRAINT fk_payments_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='付款（1 筆訂單實測無付款紀錄，屬已知資料品質）';

CREATE TABLE order_reviews (
    review_id               CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    order_id                CHAR(32) CHARACTER SET ascii COLLATE ascii_bin NOT NULL,
    review_score            TINYINT NOT NULL COMMENT '實測 1..5',
    review_comment_title    VARCHAR(64) NULL COMMENT '實測最長 26 字元',
    review_comment_message  TEXT NULL COMMENT '實測最長 208 字元；TEXT 留彈性。多行/逗號/引號由 CSV quoting 正確處理',
    review_creation_date    DATETIME NOT NULL,
    review_answer_timestamp DATETIME NOT NULL,
    PRIMARY KEY (review_id, order_id),
    KEY idx_reviews_order (order_id) COMMENT 'PK 左前綴吃不到；FK 需要',
    CONSTRAINT fk_reviews_order FOREIGN KEY (order_id) REFERENCES orders (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci
  COMMENT='評論（review_id 可跨訂單重複，複合 PK）';
