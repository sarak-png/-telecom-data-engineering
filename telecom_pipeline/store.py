"""Transactional local ingestion ledger; no external warehouse connection."""
import hashlib
import json
import sqlite3


def connect(path):
    connection = sqlite3.connect(path, timeout=30)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.executescript("""
        CREATE TABLE IF NOT EXISTS batches (
            batch_id TEXT PRIMARY KEY, source TEXT NOT NULL, started_at TEXT NOT NULL,
            finished_at TEXT, status TEXT NOT NULL, source_sha256 TEXT,
            report_json TEXT, error TEXT
        );
        CREATE TABLE IF NOT EXISTS records (
            record_hash TEXT PRIMARY KEY, payload_json TEXT NOT NULL,
            first_batch_id TEXT NOT NULL REFERENCES batches(batch_id), ingested_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS batch_records (
            batch_id TEXT NOT NULL REFERENCES batches(batch_id),
            record_hash TEXT NOT NULL REFERENCES records(record_hash),
            PRIMARY KEY (batch_id, record_hash)
        );
    """)
    return connection


def persist(connection, batch_id, records, timestamp):
    inserted = 0
    for record in records:
        payload = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        cursor = connection.execute("INSERT OR IGNORE INTO records VALUES (?, ?, ?, ?)",
                                    (digest, payload, batch_id, timestamp))
        inserted += cursor.rowcount
        connection.execute("INSERT INTO batch_records VALUES (?, ?)", (batch_id, digest))
    return inserted
