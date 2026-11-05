from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill outbound history and notify staff about open handoffs."
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    history_count = 0
    notification_count = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        outbox_rows = connection.execute(
            """
            SELECT * FROM outbox_messages
            WHERE status = 'sent' AND message_type IN ('text', 'image')
            ORDER BY sent_at, created_at
            """
        ).fetchall()
        for row in outbox_rows:
            payload = json.loads(str(row["payload"]))
            message_type = str(row["message_type"])
            if message_type == "text":
                text_content = str(payload.get("text") or "")
            else:
                text_content = f"[已发送{payload.get('caption') or '图片'}]"
            sent_at = str(row["sent_at"] or row["created_at"])
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO messages (
                    id, conversation_id, external_message_id, actor,
                    message_type, text_content, raw_payload, occurred_at,
                    received_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(row["conversation_id"]),
                    str(row["external_message_id"] or f"outbox:{row['id']}"),
                    str(row["actor"]),
                    message_type,
                    text_content,
                    json.dumps({"outbox_id": str(row["id"])}, ensure_ascii=False),
                    sent_at,
                    sent_at,
                    now,
                ),
            )
            history_count += cursor.rowcount

        tasks = connection.execute(
            """
            SELECT t.id AS task_id, t.conversation_id, c.handoff_reason
            FROM conversation_tasks t
            JOIN conversations c ON c.id = t.conversation_id
            WHERE t.task_type = 'take_over_conversation' AND t.status = 'open'
              AND c.owner IN ('human_pending', 'human')
            """
        ).fetchall()
        for task in tasks:
            task_id = str(task["task_id"])
            reason = str(task["handoff_reason"] or "顾客要求人工回复")
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO outbox_messages (
                    id, conversation_id, actor, idempotency_key, message_type,
                    payload, status, available_at, created_at, updated_at
                ) VALUES (?, ?, 'system', ?, 'staff_textcard', ?, 'pending', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    str(task["conversation_id"]),
                    f"staff-handoff:{task_id}",
                    json.dumps(
                        {
                            "task_id": task_id,
                            "title": "客户请求人工接管",
                            "description": f"接管原因：{reason[:120]}。请打开查看最近对话并处理。",
                        },
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                    now,
                ),
            )
            notification_count += cursor.rowcount
        connection.commit()
    finally:
        connection.close()
    print(f"history_backfilled={history_count} handoff_notifications={notification_count}")


if __name__ == "__main__":
    main()
