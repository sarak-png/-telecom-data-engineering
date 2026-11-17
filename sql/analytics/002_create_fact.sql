-- REVIEW DRAFT. Requires approval before manual execution.
-- CREATE fails if this object already exists. Review its definition separately.
-- FLOAT matches the confirmed STAGING types and retains nullable measurements.
CREATE TABLE TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY (
    YEAR NUMBER(38,0) NOT NULL,
    QUARTER NUMBER(38,0) NOT NULL,
    QUARTER_START_DATE DATE NOT NULL,
    OUTGOING_INT_MIN_M FLOAT,
    INCOMING_INT_MIN_M FLOAT,
    MOBILE_LOCAL_MIN_M FLOAT,
    FIXED_LOCAL_MIN_M FLOAT,
    FIXED_BROADBAND_GB_M FLOAT,
    MOBILE_BROADBAND_GB_M FLOAT,
    INTERNET_CAPACITY_MBPS FLOAT,
    SOURCE_TABLE VARCHAR NOT NULL,
    ANALYTICS_BATCH_ID VARCHAR NOT NULL,
    ANALYTICS_LOADED_AT TIMESTAMP_TZ NOT NULL
)
COMMENT = 'Quarterly Oman TRA metrics projected from STAGING.TRA_TRAFFIC_CLEAN with preserved NULLs and Analytics execution lineage';
