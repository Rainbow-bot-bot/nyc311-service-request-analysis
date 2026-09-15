-- 1. 各机构处理的工单类型及占比
DROP TABLE IF EXISTS analysis_agency_complaint;
CREATE TABLE analysis_agency_complaint AS
WITH complaint_counts AS (
    SELECT
           agency,
           agency_name_standard,
           complaint_type,
           COUNT(*) AS work_order_count
    FROM power_bi_work_orders
    WHERE complaint_type IS NOT NULL
    GROUP BY agency, agency_name_standard, complaint_type
),
complaint_shares AS (
    SELECT
        agency,
        agency_name_standard,
        complaint_type,
        work_order_count,
        SUM(work_order_count) OVER (PARTITION BY agency) AS agency_total,
        SUM(work_order_count) OVER (PARTITION BY complaint_type) AS complaint_total
    FROM complaint_counts
)
SELECT
    agency,
    agency_name_standard,
    complaint_type,
    work_order_count,
    agency_total,
    complaint_total,
    ROUND(work_order_count / agency_total * 100, 2) AS agency_percentage,
    ROUND(work_order_count / complaint_total * 100, 2) AS complaint_agency_percentage
FROM complaint_shares;

-- 2. 同类工单在不同机构的关闭时间
DROP TABLE IF EXISTS analysis_cross_agency_efficiency;
CREATE TABLE analysis_cross_agency_efficiency AS
WITH comparable_types AS (
    SELECT complaint_type
    FROM power_bi_work_orders
    WHERE complaint_type IS NOT NULL
    GROUP BY complaint_type
    HAVING COUNT(DISTINCT agency) > 1
)
SELECT
    w.complaint_type,
    w.agency,
    w.agency_name_standard,
    COUNT(*) AS closed_order_count,
    ROUND(AVG(w.closure_duration_minutes) / 60, 2) AS average_closure_hours
FROM power_bi_work_orders AS w
INNER JOIN comparable_types AS c
    ON w.complaint_type = c.complaint_type
WHERE w.closure_duration_minutes IS NOT NULL
GROUP BY w.complaint_type, w.agency, w.agency_name_standard;

-- 3. 各行政区的工单类型、数量和关闭时间
DROP TABLE IF EXISTS analysis_borough_complaint;
CREATE TABLE analysis_borough_complaint AS
WITH borough_complaints AS (
    SELECT
        borough,
        complaint_type,
        COUNT(*) AS work_order_count,
        COUNT(closed_date) AS closed_order_count,
        ROUND(AVG(closure_duration_minutes) / 60, 2) AS average_closure_hours
    FROM power_bi_work_orders
    WHERE borough != 'Unspecified'
      AND complaint_type IS NOT NULL
    GROUP BY borough, complaint_type
)
SELECT
    borough,
    complaint_type,
    work_order_count,
    closed_order_count,
    average_closure_hours,
    ROUND(work_order_count / SUM(work_order_count)
        OVER (PARTITION BY borough) * 100, 2) AS borough_percentage,
    ROUND(work_order_count / SUM(work_order_count)
        OVER (PARTITION BY complaint_type) * 100, 2) AS complaint_borough_percentage
FROM borough_complaints;

-- 4. 保留具体工单类型，分组在报表中选择
DROP TABLE IF EXISTS analysis_complaint_mix;
CREATE TABLE analysis_complaint_mix AS
WITH complaint_counts AS (
    SELECT
        COALESCE(complaint_type, 'Unknown') AS complaint_group,
        COUNT(*) AS work_order_count
    FROM power_bi_work_orders
    GROUP BY COALESCE(complaint_type, 'Unknown')
)
SELECT
    complaint_group,
    work_order_count,
    ROUND(work_order_count / SUM(work_order_count) OVER () * 100, 2)
        AS work_order_percentage
FROM complaint_counts;

-- 5. 各类工单的关闭率和平均关闭时间
DROP TABLE IF EXISTS analysis_complaint_efficiency;
CREATE TABLE analysis_complaint_efficiency AS
SELECT
    complaint_type,
    COUNT(*) AS work_order_count,
    COUNT(closed_date) AS closed_order_count,
    ROUND(COUNT(closed_date) / COUNT(*) * 100, 2) AS closed_percentage,
    ROUND(AVG(closure_duration_minutes) / 60, 2) AS average_closure_hours
FROM power_bi_work_orders
WHERE complaint_type IS NOT NULL
GROUP BY complaint_type;

-- 6. 最短25%与最长25%工单的构成
DROP TABLE IF EXISTS analysis_duration_profiles;
CREATE TABLE analysis_duration_profiles AS
WITH duration_orders AS (
    SELECT
        complaint_type,
        agency,
        borough,
        open_data_channel_type,
        DAYOFWEEK(created_date) AS created_weekday,
        HOUR(created_date) AS created_hour,
        NTILE(4) OVER (ORDER BY closure_duration_minutes) AS duration_quartile
    FROM power_bi_work_orders
    WHERE closure_duration_minutes IS NOT NULL
)
SELECT
    CASE
        WHEN duration_quartile = 1 THEN 'Short'
        WHEN duration_quartile = 4 THEN 'Long'
    END AS duration_group,
    complaint_type,
    agency,
    borough,
    open_data_channel_type,
    created_weekday,
    created_hour,
    COUNT(*) AS work_order_count
FROM duration_orders
WHERE duration_quartile IN (1, 4)
GROUP BY
    duration_group,
    complaint_type,
    agency,
    borough,
    open_data_channel_type,
    created_weekday,
    created_hour;

-- 7. 渠道、问题类型、机构和提交时间的联合结果
DROP TABLE IF EXISTS analysis_channel_response;
CREATE TABLE analysis_channel_response AS
SELECT
    open_data_channel_type,
    complaint_type,
    agency,
    DAYOFWEEK(created_date) AS created_weekday,
    HOUR(created_date) AS created_hour,
    COUNT(*) AS work_order_count,
    COUNT(closed_date) AS closed_order_count,
    ROUND(COUNT(closed_date) / COUNT(*) * 100, 2) AS closed_percentage,
    ROUND(AVG(closure_duration_minutes) / 60, 2) AS average_closure_hours
FROM power_bi_work_orders
WHERE complaint_type IS NOT NULL
GROUP BY
    open_data_channel_type,
    complaint_type,
    agency,
    created_weekday,
    created_hour;
