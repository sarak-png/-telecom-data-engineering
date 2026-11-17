-- MANUAL EXECUTION ONLY. Prepared from data/raw/tra-open-data-traffic.csv.
-- User reports TELECOM_DB.RAW already exists. No database/schema creation here.
-- Source-only landing table: nine columns in the original CSV order.
-- VARCHAR preserves formatting and precision for the RAW layer.
-- No OR REPLACE / DROP / TRUNCATE. If this table exists, CREATE fails safely.
-- This is separate from the earlier metadata-enriched RAW.TRA_TRAFFIC design.
CREATE TABLE TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE (
    "Year" VARCHAR,
    "Quarter" VARCHAR,
    "Outgoing International Minutes Million" VARCHAR,
    "Incoming International Minutes Million" VARCHAR,
    "Mobile Local Minutes Million" VARCHAR,
    "Fixed Local Minutes Million" VARCHAR,
    "Fixed Broadband Consumption Million Gb" VARCHAR,
    "Mobile Broadband Consumption Million Gb" VARCHAR,
    "International Internet Capacity Mbps" VARCHAR
)
COMMENT = 'TRA quarterly traffic source values; nine-column RAW landing table. Preserve source formatting; validate and type in STAGING.';
