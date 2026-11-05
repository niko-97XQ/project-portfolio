from __future__ import annotations

import sqlite3
from pathlib import Path


def main() -> int:
    for path in (Path("data/app.db"), Path("data/appointments.db")):
        if not path.exists():
            continue
        print(f"database={path} size={path.stat().st_size}")
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        print("tables=" + ",".join(sorted(tables)))
        for table in (
            "appointments",
            "chat_messages",
            "conversations",
            "processed_wecom_messages",
        ):
            if table in tables:
                count = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                print(f"{table}_count={count}")
        if "appointments" in tables:
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(appointments)")
            }
            print("appointment_columns=" + ",".join(sorted(columns)))
            if "status" in columns:
                rows = connection.execute(
                    "SELECT status, COUNT(*) FROM appointments GROUP BY status"
                ).fetchall()
                print(
                    "appointment_statuses="
                    + ",".join(f"{status}:{count}" for status, count in rows)
                )
            if "created_at" in columns:
                oldest, newest = connection.execute(
                    "SELECT MIN(created_at), MAX(created_at) FROM appointments"
                ).fetchone()
                print(f"appointment_created_range={oldest}..{newest}")
            if "conversation_id" in columns:
                wecom_count = connection.execute(
                    "SELECT COUNT(*) FROM appointments WHERE conversation_id LIKE 'wecom-kf:%'"
                ).fetchone()[0]
                empty_count = connection.execute(
                    "SELECT COUNT(*) FROM appointments WHERE conversation_id = '' OR conversation_id IS NULL"
                ).fetchone()[0]
                print(f"appointment_sources=wecom:{wecom_count},empty:{empty_count}")
        connection.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
