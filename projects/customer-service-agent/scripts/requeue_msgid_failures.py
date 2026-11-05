from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Requeue outbox rows rejected by WeCom's old oversized msgid."
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(args.database)
    try:
        cursor = connection.execute(
            """
            UPDATE outbox_messages
            SET status = 'pending', attempt_count = 0, available_at = ?,
                locked_at = NULL, locked_by = '', last_error = '', updated_at = ?
            WHERE status = 'dead'
              AND last_error LIKE '%msgid exceed max length 32%'
            """,
            (now, now),
        )
        connection.commit()
        print(f"requeued={cursor.rowcount}")
    finally:
        connection.close()


if __name__ == "__main__":
    main()
