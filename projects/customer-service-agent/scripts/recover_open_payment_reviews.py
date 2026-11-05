from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue missing staff notifications and customer updates for open payment reviews."
    )
    parser.add_argument("database", type=Path)
    args = parser.parse_args()

    now = datetime.now(timezone.utc).isoformat()
    connection = sqlite3.connect(args.database)
    connection.row_factory = sqlite3.Row
    staff_count = 0
    customer_count = 0
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT t.id AS task_id, t.conversation_id, a.customer_name
            FROM conversation_tasks t
            JOIN appointments a ON a.id = t.appointment_id
            WHERE t.task_type = 'review_payment' AND t.status = 'open'
              AND a.status = 'payment_review'
            """
        ).fetchall()
        for row in rows:
            task_id = str(row["task_id"])
            common = (
                str(uuid4()),
                str(row["conversation_id"]),
                now,
                now,
                now,
            )
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO outbox_messages (
                    id, conversation_id, actor, idempotency_key, message_type,
                    payload, status, available_at, created_at, updated_at
                ) VALUES (?, ?, 'system', ?, 'staff_textcard', ?, 'pending', ?, ?, ?)
                """,
                (
                    common[0],
                    common[1],
                    f"staff-payment-review:{task_id}",
                    json.dumps(
                        {
                            "task_id": task_id,
                            "title": "付款截图待复核",
                            "description": (
                                f"{row['customer_name']}的付款截图未通过自动核对，"
                                "请查看原图并确认处理结果。"
                            ),
                        },
                        ensure_ascii=False,
                    ),
                    common[2],
                    common[3],
                    common[4],
                ),
            )
            staff_count += cursor.rowcount
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO outbox_messages (
                    id, conversation_id, actor, idempotency_key, message_type,
                    payload, status, available_at, created_at, updated_at
                ) VALUES (?, ?, 'system', ?, 'text', ?, 'pending', ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    common[1],
                    f"payment-review-correction:{task_id}",
                    json.dumps(
                        {
                            "text": (
                                "这张截图里的付款信息没有完整识别到，档期暂时还没有锁定。"
                                "麻烦重新发一张完整的支付成功页面，需要清楚显示200元金额、"
                                "收款方和支付成功状态。我也已经提醒店员复核。"
                            )
                        },
                        ensure_ascii=False,
                    ),
                    now,
                    now,
                    now,
                ),
            )
            customer_count += cursor.rowcount
        connection.commit()
    finally:
        connection.close()
    print(f"staff_notifications={staff_count} customer_updates={customer_count}")


if __name__ == "__main__":
    main()
