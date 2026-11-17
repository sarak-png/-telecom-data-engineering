-- REVIEW DRAFT. Execute as one complete block only after approval.
-- Current-snapshot contract is 66 periods spanning 2010 Q1 through 2026 Q2.
-- Run with all source and target writers paused. Use a fresh worksheet transaction.
-- All STAGING references are reads. Target writes are insert-only ANALYTICS MERGE.
-- FLOAT comparisons below check direct stored-value copies without arithmetic.
EXECUTE IMMEDIATE $$
DECLARE
    invalid_source EXCEPTION (-20001, 'STAGING snapshot failed the approved 66-period quality baseline');
    conflicting_target EXCEPTION (-20002, 'Analytics contains duplicate, extra, or conflicting periods');
    reconciliation_failed EXCEPTION (-20003, 'Analytics read-back reconciliation failed');
    active_transaction EXCEPTION (-20004, 'Finish the existing worksheet transaction before running this load');
    batch_id VARCHAR DEFAULT UUID_STRING();
    loaded_at TIMESTAMP_TZ DEFAULT CONVERT_TIMEZONE('UTC', CURRENT_TIMESTAMP());
    invalid_count INTEGER;
    row_count INTEGER;
    before_count INTEGER;
    after_count INTEGER;
    inserted_count INTEGER;
    batch_row_count INTEGER;
    existing_transaction NUMBER(38,0);
    transaction_started BOOLEAN DEFAULT FALSE;
    result OBJECT;
BEGIN
    -- Avoid joining or rolling back a transaction opened by the caller.
    SELECT CURRENT_TRANSACTION() INTO :existing_transaction;
    IF (existing_transaction IS NOT NULL) THEN
        RAISE active_transaction;
    END IF;
    BEGIN TRANSACTION;
    transaction_started := TRUE;

    SELECT COUNT(*) INTO :row_count FROM TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN;
    IF (row_count <> 66) THEN
        RAISE invalid_source;
    END IF;

    SELECT COUNT(*) INTO :invalid_count
    FROM TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN
    WHERE YEAR IS NULL OR QUARTER IS NULL OR QUARTER NOT BETWEEN 1 AND 4
       OR YEAR NOT BETWEEN 2010 AND 2026;
    IF (invalid_count <> 0) THEN
        RAISE invalid_source;
    END IF;

    SELECT COUNT(*) INTO :invalid_count FROM (
        SELECT YEAR, QUARTER FROM TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN
        GROUP BY YEAR, QUARTER HAVING COUNT(*) > 1
    );
    IF (invalid_count <> 0) THEN
        RAISE invalid_source;
    END IF;

    -- Compare both directions against the exact consecutive quarter set.
    -- ROW_NUMBER supplies gap-free offsets even if SEQ4 itself has gaps.
    WITH quarter_dates AS (
        SELECT DATEADD(QUARTER, ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1,
                       DATE_FROM_PARTS(2010, 1, 1))::DATE AS QUARTER_START_DATE
        FROM TABLE(GENERATOR(ROWCOUNT => 66))
    ), expected_periods AS (
        SELECT YEAR(QUARTER_START_DATE) AS YEAR,
               QUARTER(QUARTER_START_DATE) AS QUARTER
        FROM quarter_dates
    )
    SELECT COUNT(*) INTO :invalid_count
    FROM expected_periods AS e
    FULL OUTER JOIN TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN AS s
      ON e.YEAR = s.YEAR AND e.QUARTER = s.QUARTER
    WHERE e.YEAR IS NULL OR s.YEAR IS NULL;
    IF (invalid_count <> 0) THEN
        RAISE invalid_source;
    END IF;

    WITH source_null_counts AS (
        SELECT COUNT(*) - COUNT(OUTGOING_INT_MIN_M) AS OUTGOING_NULLS,
               COUNT(*) - COUNT(INCOMING_INT_MIN_M) AS INCOMING_NULLS,
               COUNT(*) - COUNT(MOBILE_LOCAL_MIN_M) AS MOBILE_LOCAL_NULLS,
               COUNT(*) - COUNT(FIXED_LOCAL_MIN_M) AS FIXED_LOCAL_NULLS,
               COUNT(*) - COUNT(FIXED_BROADBAND_GB_M) AS FIXED_BROADBAND_NULLS,
               COUNT(*) - COUNT(MOBILE_BROADBAND_GB_M) AS MOBILE_BROADBAND_NULLS,
               COUNT(*) - COUNT(INTERNET_CAPACITY_MBPS) AS CAPACITY_NULLS
        FROM TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN
    )
    SELECT COUNT(*) INTO :invalid_count FROM source_null_counts
    WHERE OUTGOING_NULLS <> 0 OR INCOMING_NULLS <> 0
       OR MOBILE_LOCAL_NULLS <> 4 OR FIXED_LOCAL_NULLS <> 15
       OR FIXED_BROADBAND_NULLS <> 32 OR MOBILE_BROADBAND_NULLS <> 32
       OR CAPACITY_NULLS <> 1;
    IF (invalid_count <> 0) THEN
        RAISE invalid_source;
    END IF;

    SELECT COUNT(*) INTO :invalid_count
    FROM TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN
    UNPIVOT INCLUDE NULLS (METRIC_VALUE FOR METRIC_NAME IN (
        OUTGOING_INT_MIN_M, INCOMING_INT_MIN_M, MOBILE_LOCAL_MIN_M,
        FIXED_LOCAL_MIN_M, FIXED_BROADBAND_GB_M, MOBILE_BROADBAND_GB_M,
        INTERNET_CAPACITY_MBPS
    ))
    WHERE METRIC_VALUE < 0 OR METRIC_VALUE IN ('NaN'::FLOAT, 'inf'::FLOAT, '-inf'::FLOAT);
    IF (invalid_count <> 0) THEN
        RAISE invalid_source;
    END IF;

    SELECT COUNT(*) INTO :before_count FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY;
    SELECT COUNT(*) INTO :invalid_count FROM (
        SELECT YEAR, QUARTER FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY
        GROUP BY YEAR, QUARTER HAVING COUNT(*) > 1
    );
    IF (invalid_count <> 0) THEN
        RAISE conflicting_target;
    END IF;

    SELECT COUNT(*) INTO :invalid_count
    FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY AS t
    LEFT JOIN TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN AS s
      ON t.YEAR = s.YEAR AND t.QUARTER = s.QUARTER
    WHERE s.YEAR IS NULL
       OR t.QUARTER_START_DATE IS DISTINCT FROM DATE_FROM_PARTS(s.YEAR, (s.QUARTER - 1) * 3 + 1, 1)
       OR t.SOURCE_TABLE IS DISTINCT FROM 'TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN'
       OR NULLIF(TRIM(t.ANALYTICS_BATCH_ID), '') IS NULL
       OR t.ANALYTICS_LOADED_AT IS NULL
       OR t.OUTGOING_INT_MIN_M IS DISTINCT FROM s.OUTGOING_INT_MIN_M
       OR t.INCOMING_INT_MIN_M IS DISTINCT FROM s.INCOMING_INT_MIN_M
       OR t.MOBILE_LOCAL_MIN_M IS DISTINCT FROM s.MOBILE_LOCAL_MIN_M
       OR t.FIXED_LOCAL_MIN_M IS DISTINCT FROM s.FIXED_LOCAL_MIN_M
       OR t.FIXED_BROADBAND_GB_M IS DISTINCT FROM s.FIXED_BROADBAND_GB_M
       OR t.MOBILE_BROADBAND_GB_M IS DISTINCT FROM s.MOBILE_BROADBAND_GB_M
       OR t.INTERNET_CAPACITY_MBPS IS DISTINCT FROM s.INTERNET_CAPACITY_MBPS;
    IF (invalid_count <> 0) THEN
        RAISE conflicting_target;
    END IF;

    MERGE INTO TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY AS t
    USING TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN AS s
      ON t.YEAR = s.YEAR AND t.QUARTER = s.QUARTER
    WHEN NOT MATCHED THEN INSERT (
        YEAR, QUARTER, QUARTER_START_DATE,
        OUTGOING_INT_MIN_M, INCOMING_INT_MIN_M, MOBILE_LOCAL_MIN_M,
        FIXED_LOCAL_MIN_M, FIXED_BROADBAND_GB_M, MOBILE_BROADBAND_GB_M,
        INTERNET_CAPACITY_MBPS, SOURCE_TABLE, ANALYTICS_BATCH_ID, ANALYTICS_LOADED_AT
    ) VALUES (
        s.YEAR, s.QUARTER, DATE_FROM_PARTS(s.YEAR, (s.QUARTER - 1) * 3 + 1, 1),
        s.OUTGOING_INT_MIN_M, s.INCOMING_INT_MIN_M, s.MOBILE_LOCAL_MIN_M,
        s.FIXED_LOCAL_MIN_M, s.FIXED_BROADBAND_GB_M, s.MOBILE_BROADBAND_GB_M,
        s.INTERNET_CAPACITY_MBPS, 'TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN', :batch_id, :loaded_at
    );
    -- Capture immediately because subsequent SELECT statements reset SQLROWCOUNT.
    inserted_count := SQLROWCOUNT;

    SELECT COUNT(*) INTO :after_count FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY;
    SELECT COUNT(*) INTO :invalid_count
    FROM TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN AS s
    FULL OUTER JOIN TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY AS t
      ON t.YEAR = s.YEAR AND t.QUARTER = s.QUARTER
    WHERE s.YEAR IS NULL OR t.YEAR IS NULL
       OR t.QUARTER_START_DATE IS DISTINCT FROM DATE_FROM_PARTS(s.YEAR, (s.QUARTER - 1) * 3 + 1, 1)
       OR t.SOURCE_TABLE IS DISTINCT FROM 'TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN'
       OR NULLIF(TRIM(t.ANALYTICS_BATCH_ID), '') IS NULL
       OR t.ANALYTICS_LOADED_AT IS NULL
       OR t.OUTGOING_INT_MIN_M IS DISTINCT FROM s.OUTGOING_INT_MIN_M
       OR t.INCOMING_INT_MIN_M IS DISTINCT FROM s.INCOMING_INT_MIN_M
       OR t.MOBILE_LOCAL_MIN_M IS DISTINCT FROM s.MOBILE_LOCAL_MIN_M
       OR t.FIXED_LOCAL_MIN_M IS DISTINCT FROM s.FIXED_LOCAL_MIN_M
       OR t.FIXED_BROADBAND_GB_M IS DISTINCT FROM s.FIXED_BROADBAND_GB_M
       OR t.MOBILE_BROADBAND_GB_M IS DISTINCT FROM s.MOBILE_BROADBAND_GB_M
       OR t.INTERNET_CAPACITY_MBPS IS DISTINCT FROM s.INTERNET_CAPACITY_MBPS;
    IF (after_count <> 66 OR invalid_count <> 0
        OR inserted_count IS NULL OR inserted_count <> after_count - before_count) THEN
        RAISE reconciliation_failed;
    END IF;

    SELECT COUNT(*) INTO :invalid_count FROM (
        SELECT YEAR, QUARTER FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY
        GROUP BY YEAR, QUARTER HAVING COUNT(*) > 1
    );
    IF (invalid_count <> 0) THEN
        RAISE reconciliation_failed;
    END IF;

    -- Recheck the target against the same exact calendar independently of source.
    WITH quarter_dates AS (
        SELECT DATEADD(QUARTER, ROW_NUMBER() OVER (ORDER BY SEQ4()) - 1,
                       DATE_FROM_PARTS(2010, 1, 1))::DATE AS QUARTER_START_DATE
        FROM TABLE(GENERATOR(ROWCOUNT => 66))
    ), expected_periods AS (
        SELECT YEAR(QUARTER_START_DATE) AS YEAR,
               QUARTER(QUARTER_START_DATE) AS QUARTER
        FROM quarter_dates
    )
    SELECT COUNT(*) INTO :invalid_count
    FROM expected_periods AS e
    FULL OUTER JOIN TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY AS t
      ON e.YEAR = t.YEAR AND e.QUARTER = t.QUARTER
    WHERE e.YEAR IS NULL OR t.YEAR IS NULL;
    IF (invalid_count <> 0) THEN
        RAISE reconciliation_failed;
    END IF;

    -- Independently reconcile all seven target NULL counts before commit.
    WITH target_null_counts AS (
        SELECT COUNT(*) - COUNT(OUTGOING_INT_MIN_M) AS OUTGOING_NULLS,
               COUNT(*) - COUNT(INCOMING_INT_MIN_M) AS INCOMING_NULLS,
               COUNT(*) - COUNT(MOBILE_LOCAL_MIN_M) AS MOBILE_LOCAL_NULLS,
               COUNT(*) - COUNT(FIXED_LOCAL_MIN_M) AS FIXED_LOCAL_NULLS,
               COUNT(*) - COUNT(FIXED_BROADBAND_GB_M) AS FIXED_BROADBAND_NULLS,
               COUNT(*) - COUNT(MOBILE_BROADBAND_GB_M) AS MOBILE_BROADBAND_NULLS,
               COUNT(*) - COUNT(INTERNET_CAPACITY_MBPS) AS CAPACITY_NULLS
        FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY
    )
    SELECT COUNT(*) INTO :invalid_count FROM target_null_counts
    WHERE OUTGOING_NULLS <> 0 OR INCOMING_NULLS <> 0
       OR MOBILE_LOCAL_NULLS <> 4 OR FIXED_LOCAL_NULLS <> 15
       OR FIXED_BROADBAND_NULLS <> 32 OR MOBILE_BROADBAND_NULLS <> 32
       OR CAPACITY_NULLS <> 1;
    IF (invalid_count <> 0) THEN
        RAISE reconciliation_failed;
    END IF;

    SELECT COUNT(*) INTO :batch_row_count
    FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY
    WHERE ANALYTICS_BATCH_ID = :batch_id;
    SELECT COUNT(*) INTO :invalid_count
    FROM TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY
    WHERE ANALYTICS_BATCH_ID = :batch_id
      AND ANALYTICS_LOADED_AT IS DISTINCT FROM :loaded_at;
    IF (batch_row_count <> inserted_count OR invalid_count <> 0) THEN
        RAISE reconciliation_failed;
    END IF;

    -- Construct the return value before committing the validated inserts.
    result := OBJECT_CONSTRUCT('status', 'SUCCEEDED', 'batch_id', batch_id,
                               'inserted_rows', inserted_count,
                               'verified_rows', after_count);
    COMMIT;
    transaction_started := FALSE;
    RETURN result;
EXCEPTION
    WHEN OTHER THEN
        IF (transaction_started) THEN
            -- A rollback error must not replace the outer validation/SQL error.
            -- If cleanup fails, inspect the session transaction before retrying.
            BEGIN
                ROLLBACK;
            EXCEPTION
                WHEN OTHER THEN
                    NULL;
            END;
        END IF;
        -- Rethrow the original exception from this outer handler.
        RAISE;
END;
$$;
