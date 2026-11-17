"""Read-only contract checks. SQL files are inspected as text."""
import ast
import hashlib
import json
from pathlib import Path
import re
import sys

import pytest

from telecom_pipeline import snowflake_loader
from telecom_pipeline.ingestion import load_tra_csv
from telecom_pipeline.snowflake_config import DEFAULT_DATABASE, WAREHOUSE_CONTRACT
from telecom_pipeline.validation import COLUMNS


ROOT = Path(__file__).resolve().parents[1]
OBJECTS = WAREHOUSE_CONTRACT["objects"]
STAGING_COLUMNS = (
    "YEAR", "QUARTER", "OUTGOING_INT_MIN_M", "INCOMING_INT_MIN_M",
    "MOBILE_LOCAL_MIN_M", "FIXED_LOCAL_MIN_M", "FIXED_BROADBAND_GB_M",
    "MOBILE_BROADBAND_GB_M", "INTERNET_CAPACITY_MBPS",
)


def test_exact_confirmed_object_set():
    actual = {
        f"{DEFAULT_DATABASE}.{obj['schema']}.{obj['name']}"
        for obj in OBJECTS.values()
    }
    assert actual == {
        "TELECOM_DB.RAW.TRA_TRAFFIC_SOURCE",
        "TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN",
        "TELECOM_DB.ANALYTICS.FACT_TELECOM_QUARTERLY",
        "TELECOM_DB.ANALYTICS.VW_YEARLY_TELECOM_SUMMARY",
        "TELECOM_DB.ANALYTICS.VW_TELECOM_DATA_COVERAGE",
    }


def test_contract_file_is_the_configuration_source():
    stored = json.loads((ROOT / "config/warehouse_contract.json").read_text(encoding="utf-8"))
    assert WAREHOUSE_CONTRACT == stored
    assert DEFAULT_DATABASE == stored["database"]
    assert f"SNOWFLAKE_DATABASE={DEFAULT_DATABASE}" in (ROOT / ".env.example").read_text()


def test_raw_columns_match_the_observed_csv_contract():
    columns = OBJECTS["raw_source"]["columns"]
    assert tuple(column["name"] for column in columns) == COLUMNS
    assert all(column["type"] == "VARCHAR(16777216)" and column["nullable"] for column in columns)


def test_staging_matches_supplied_describe_metadata():
    staging = OBJECTS["staging_clean"]
    assert staging["schema_status"] == "confirmed_by_describe_export"
    assert tuple(column["name"] for column in staging["columns"]) == STAGING_COLUMNS
    assert [column["type"] for column in staging["columns"]] == ["NUMBER(38,0)"] * 2 + ["FLOAT"] * 7
    assert all(column["nullable"] for column in staging["columns"])


def test_mapping_covers_every_source_and_staging_column_once():
    mapping = WAREHOUSE_CONTRACT["column_mapping"]
    assert tuple(item["raw"] for item in mapping) == COLUMNS
    assert tuple(item["staging"] for item in mapping) == STAGING_COLUMNS
    assert tuple(item["analytics"] for item in mapping) == STAGING_COLUMNS
    assert len({item["raw"] for item in mapping}) == 9
    assert len({item["staging"] for item in mapping}) == 9


@pytest.mark.parametrize("key,filename", [
    ("raw_source", "sql/006_create_raw_tra_traffic_source.sql"),
    ("quarterly_fact", "sql/analytics/002_create_fact.sql"),
])
def test_declared_table_contracts_match_preserved_ddl(key, filename):
    sql = (ROOT / filename).read_text(encoding="utf-8")
    parsed = []
    pattern = r'^\s+(?:"([^"]+)"|([A-Z_]+))\s+(NUMBER\(38,0\)|VARCHAR|FLOAT|DATE|TIMESTAMP_TZ)(\s+NOT NULL)?\s*,?\s*$'
    # Compare the historical declarations with the explicit defaults now supplied
    # by DESCRIBE. The original SQL retains its unparameterized declarations.
    described_defaults = {"VARCHAR": "VARCHAR(16777216)", "TIMESTAMP_TZ": "TIMESTAMP_TZ(9)"}
    for match in re.finditer(pattern, sql, re.MULTILINE):
        quoted_name, name, data_type, required = match.groups()
        parsed.append({"name": quoted_name or name, "type": described_defaults.get(data_type, data_type), "nullable": required is None})
    assert parsed == OBJECTS[key]["columns"]
    assert OBJECTS[key]["schema_status"] == "verified_from_live_describe"


@pytest.mark.parametrize("key", ["raw_source", "quarterly_fact"])
def test_verified_describe_evidence_and_absent_keys(key):
    obj = OBJECTS[key]
    assert obj["schema_status"] == "verified_from_live_describe"
    assert "User-supplied verified live Snowflake DESCRIBE results" in obj["schema_evidence"]
    assert obj["constraints"] == {"primary_key": [], "unique_keys": []}


def test_fact_describe_precision_and_required_metadata():
    fact = {column["name"]: column for column in OBJECTS["quarterly_fact"]["columns"]}
    expected = {
        "YEAR": "NUMBER(38,0)", "QUARTER": "NUMBER(38,0)",
        "QUARTER_START_DATE": "DATE", "SOURCE_TABLE": "VARCHAR(16777216)",
        "ANALYTICS_BATCH_ID": "VARCHAR(16777216)", "ANALYTICS_LOADED_AT": "TIMESTAMP_TZ(9)",
    }
    for name, data_type in expected.items():
        assert fact[name] == {"name": name, "type": data_type, "nullable": False}


@pytest.mark.parametrize("key", ["yearly_summary", "data_coverage"])
def test_view_projection_names_match_sql_and_unknown_types_are_explicit(key):
    sql = (ROOT / "sql/analytics/004_create_views.sql").read_text(encoding="utf-8")
    definition = sql.split(f"CREATE VIEW {DEFAULT_DATABASE}.ANALYTICS.{OBJECTS[key]['name']} AS", 1)[1].split(";", 1)[0]
    projection = definition.split("FROM", 1)[0].strip()
    direct_columns = ["YEAR", "METRIC_NAME"] if key == "data_coverage" else ["YEAR"]
    assert projection.startswith("SELECT " + ", ".join(direct_columns) + ",")
    names = direct_columns + re.findall(r'\bAS\s+([A-Z][A-Z0-9_]*)', projection)
    assert names == [column["name"] for column in OBJECTS[key]["columns"]]
    assert all(column["type"] is None and column["nullable"] is None for column in OBJECTS[key]["columns"])


def test_fact_preserves_staging_metric_aliases_types_and_nullability():
    fact = {column["name"]: column for column in OBJECTS["quarterly_fact"]["columns"]}
    for name in STAGING_COLUMNS[2:]:
        assert fact[name] == {"name": name, "type": "FLOAT", "nullable": True}
    assert fact["YEAR"]["nullable"] is False
    assert fact["QUARTER"]["nullable"] is False
    assert len(fact) == 13


def test_historical_baseline_matches_immutable_source():
    path = ROOT / "data/raw/tra-open-data-traffic.csv"
    baseline = WAREHOUSE_CONTRACT["historical_baseline"]
    result = load_tra_csv(path)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == baseline["source_sha256"]
    assert result.data.shape == (baseline["row_count"], 9)
    assert baseline["first_period"] == [2010, 1]
    assert baseline["last_period"] == [2026, 2]
    mapping = WAREHOUSE_CONTRACT["column_mapping"][2:]
    actual = {item["staging"]: int(result.data[item["raw"]].isna().sum()) for item in mapping}
    assert actual == baseline["metric_null_counts"]
    assert list(actual.values()) == [0, 0, 4, 15, 32, 32, 1]


def test_legacy_objects_are_documented_without_automatic_aliases():
    legacy = WAREHOUSE_CONTRACT["legacy_objects"]
    assert {obj["name"] for obj in legacy} == {
        "RAW.TRA_TRAFFIC", "STAGING.TRA_TRAFFIC", "MART.FACT_TELECOM_QUARTERLY", "OPS.INGESTION_BATCHES",
    }
    assert all(obj["automatic_replacement"] is None and obj["reason"] for obj in legacy)
    assert len(snowflake_loader.RAW_COLUMNS) == 14
    assert len(OBJECTS["raw_source"]["columns"]) == 9


def test_offline_plan_reports_current_contract_and_legacy_executor(monkeypatch, capsys):
    def forbidden(*args, **kwargs):
        pytest.fail("An offline contract plan attempted cloud execution")

    monkeypatch.setattr(snowflake_loader, "load", forbidden)
    monkeypatch.setattr(snowflake_loader, "connect", forbidden)
    monkeypatch.delenv("SNOWFLAKE_DATABASE", raising=False)
    monkeypatch.setattr(sys, "argv", ["snowflake_loader", str(ROOT / "data/raw/tra-open-data-traffic.csv")])
    assert snowflake_loader.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "OFFLINE_PLAN"
    assert result["cloud_executed"] is False
    assert result["expected_rows"] == 66
    assert result["warehouse_contract"]["confirmed_objects"]["staging_clean"] == "TELECOM_DB.STAGING.TRA_TRAFFIC_CLEAN"
    assert result["warehouse_contract"]["executor_status"].startswith("legacy_raw_ops_adapter")


def test_connection_configuration_import_has_no_cloud_dependency():
    source = (ROOT / "telecom_pipeline/snowflake_config.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    top_level_imports = [node for node in tree.body if isinstance(node, (ast.Import, ast.ImportFrom))]
    for node in top_level_imports:
        if isinstance(node, ast.Import):
            assert all(not item.name.startswith("snowflake") for item in node.names)
        else:
            assert not (node.module or "").startswith("snowflake")
