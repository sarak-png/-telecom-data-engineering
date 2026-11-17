"""Manual opt-in only AFTER approval for cloud writes and existing setup."""
import os

import pytest

from telecom_pipeline.snowflake_config import project_database
from telecom_pipeline.snowflake_loader import load, local_writer_lock, prepare


@pytest.mark.skipif(os.environ.get("TELECOM_SNOWFLAKE_LIVE_APPROVED") != "1",
                    reason="Live cloud writes require explicit approval and configured resources")
def test_live_raw_load_and_rerun():
    database = project_database(os.environ["SNOWFLAKE_DATABASE"])
    with local_writer_lock("data/ingestion", database):
        report, records = prepare("data/raw/tra-open-data-traffic.csv")
        first = load(report, records, database=database, approved=True)
        report, records = prepare("data/raw/tra-open-data-traffic.csv")
        second = load(report, records, database=database, approved=True)
    assert first["verified_rows"] == 66
    assert second["verified_rows"] == 66
    assert second["inserted_rows"] == 0
