from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


STATUS_TABLES = (
    "wecom_callback_jobs",
    "inbox_jobs",
    "outbox_messages",
    "action_jobs",
)


def main() -> None:
    parser = argparse.ArgumentParser(description="Print a PII-free runtime queue summary.")
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    connection = sqlite3.connect(f"file:{args.database}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        report: dict[str, object] = {
            "conversations": _count(connection, "conversations"),
            "messages": _count(connection, "messages"),
        }
        for table in STATUS_TABLES:
            report[table] = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT status, COUNT(*) AS count,
                           MAX(updated_at) AS latest,
                           MAX(NULLIF(last_error, '')) AS last_error
                    FROM {table}
                    GROUP BY status
                    ORDER BY status
                    """
                )
            ]
        for table in ("appointments", "conversation_tasks"):
            report[table] = [
                dict(row)
                for row in connection.execute(
                    f"""
                    SELECT status, COUNT(*) AS count, MAX(updated_at) AS latest
                    FROM {table}
                    GROUP BY status
                    ORDER BY status
                    """
                )
            ]
        report["handoffs"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT status, COUNT(*) AS count, MAX(requested_at) AS latest
                FROM handoffs
                GROUP BY status
                ORDER BY status
                """
            )
        ]
        report["payment_evidence"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT decision, extracted_amount, shows_success, confidence,
                       decision_reasons_json AS reasons, created_at AS latest
                FROM payment_evidence
                ORDER BY created_at DESC
                LIMIT 10
                """
            )
        ]
        report["recent_audit_events"] = [
            dict(row)
            for row in connection.execute(
                """
                SELECT actor_type, event_type, created_at
                FROM audit_events
                ORDER BY created_at DESC
                LIMIT 10
                """
            )
        ]
        print(json.dumps(report, ensure_ascii=False, indent=2))
    finally:
        connection.close()


def _count(connection: sqlite3.Connection, table: str) -> int:
    return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


if __name__ == "__main__":
    main()
