-- Executed by the Python loader only after approval. Parameter is JSON text.
-- RECORD_HASH identifies (source-file bytes hash, one-based parsed data row).
-- RAW retains all data rows, including source duplicates; never updates matches.
MERGE INTO {{DATABASE}}.RAW.TRA_TRAFFIC AS target
USING (
  SELECT
    r.value:record_hash::VARCHAR AS RECORD_HASH,
    r.value:source_sha256::VARCHAR AS SOURCE_SHA256,
    r.value:row_number::NUMBER AS SOURCE_ROW_NUMBER,
    r.value:batch_id::VARCHAR AS FIRST_BATCH_ID,
    r.value:loaded_at::TIMESTAMP_TZ AS LOADED_AT,
    IFF(IS_NULL_VALUE(r.value:values[0]), NULL, r.value:values[0]::VARCHAR) AS "Year",
    IFF(IS_NULL_VALUE(r.value:values[1]), NULL, r.value:values[1]::VARCHAR) AS "Quarter",
    IFF(IS_NULL_VALUE(r.value:values[2]), NULL, r.value:values[2]::VARCHAR) AS "Outgoing International Minutes Million",
    IFF(IS_NULL_VALUE(r.value:values[3]), NULL, r.value:values[3]::VARCHAR) AS "Incoming International Minutes Million",
    IFF(IS_NULL_VALUE(r.value:values[4]), NULL, r.value:values[4]::VARCHAR) AS "Mobile Local Minutes Million",
    IFF(IS_NULL_VALUE(r.value:values[5]), NULL, r.value:values[5]::VARCHAR) AS "Fixed Local Minutes Million",
    IFF(IS_NULL_VALUE(r.value:values[6]), NULL, r.value:values[6]::VARCHAR) AS "Fixed Broadband Consumption Million Gb",
    IFF(IS_NULL_VALUE(r.value:values[7]), NULL, r.value:values[7]::VARCHAR) AS "Mobile Broadband Consumption Million Gb",
    IFF(IS_NULL_VALUE(r.value:values[8]), NULL, r.value:values[8]::VARCHAR) AS "International Internet Capacity Mbps"
  FROM TABLE(FLATTEN(INPUT => PARSE_JSON(%s))) AS r
) AS source
ON target.RECORD_HASH = source.RECORD_HASH
WHEN NOT MATCHED THEN INSERT (
  RECORD_HASH, SOURCE_SHA256, SOURCE_ROW_NUMBER, FIRST_BATCH_ID, LOADED_AT,
  "Year", "Quarter", "Outgoing International Minutes Million",
  "Incoming International Minutes Million", "Mobile Local Minutes Million",
  "Fixed Local Minutes Million", "Fixed Broadband Consumption Million Gb",
  "Mobile Broadband Consumption Million Gb", "International Internet Capacity Mbps"
) VALUES (
  source.RECORD_HASH, source.SOURCE_SHA256, source.SOURCE_ROW_NUMBER,
  source.FIRST_BATCH_ID, source.LOADED_AT,
  source."Year", source."Quarter", source."Outgoing International Minutes Million",
  source."Incoming International Minutes Million", source."Mobile Local Minutes Million",
  source."Fixed Local Minutes Million", source."Fixed Broadband Consumption Million Gb",
  source."Mobile Broadband Consumption Million Gb", source."International Internet Capacity Mbps"
);
