from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from forthatday_agent.domain.action_jobs import ActionJob
from forthatday_agent.domain.conversations import ConversationOwner
from forthatday_agent.domain.dialogue import ActionType, DialogueDecision
from forthatday_agent.domain.memory import ConversationMemory, FactSource, MemoryFact
from forthatday_agent.domain.payments import (
    PaymentDecision,
    PaymentEvaluation,
    PaymentEvidence,
)
from forthatday_agent.domain.messages import (
    InboundMessage,
    InboxBatch,
    MessageActor,
    OutboxMessage,
    ReceiveResult,
    StoredMessage,
    new_id,
)
from forthatday_agent.application.staff_appointments import (
    PaymentCodes,
    StaffHandoffMessage,
    StaffHandoffTask,
    StaffPaymentTask,
    StaffTimeTask,
)
from forthatday_agent.application.wecom_callback import WeComCallbackJob


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    channel TEXT NOT NULL,
    channel_account_id TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    owner TEXT NOT NULL DEFAULT 'ai',
    handoff_status TEXT NOT NULL DEFAULT 'none',
    handoff_reason TEXT NOT NULL DEFAULT '',
    assigned_staff_id TEXT NOT NULL DEFAULT '',
    handoff_summary TEXT NOT NULL DEFAULT '',
    last_message_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(channel, channel_account_id, external_user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    external_message_id TEXT,
    actor TEXT NOT NULL,
    message_type TEXT NOT NULL,
    text_content TEXT NOT NULL DEFAULT '',
    raw_payload TEXT NOT NULL DEFAULT '{}',
    occurred_at TEXT NOT NULL,
    received_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(conversation_id, external_message_id)
);

CREATE INDEX IF NOT EXISTS messages_conversation_timeline
ON messages(conversation_id, occurred_at, id);

CREATE TABLE IF NOT EXISTS inbox_jobs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    message_id TEXT NOT NULL UNIQUE REFERENCES messages(id),
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    locked_at TEXT,
    locked_by TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS inbox_jobs_ready
ON inbox_jobs(status, available_at, created_at);

CREATE TABLE IF NOT EXISTS outbox_messages (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    actor TEXT NOT NULL,
    idempotency_key TEXT NOT NULL UNIQUE,
    message_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    locked_at TEXT,
    locked_by TEXT NOT NULL DEFAULT '',
    external_message_id TEXT,
    sent_at TEXT,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS outbox_messages_ready
ON outbox_messages(status, available_at, created_at);

CREATE TABLE IF NOT EXISTS conversation_memory (
    conversation_id TEXT PRIMARY KEY REFERENCES conversations(id),
    summary TEXT NOT NULL DEFAULT '',
    facts_json TEXT NOT NULL DEFAULT '[]',
    pending_tasks_json TEXT NOT NULL DEFAULT '[]',
    summary_through_message_id TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS action_jobs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    source_message_id TEXT NOT NULL REFERENCES messages(id),
    action_type TEXT NOT NULL,
    arguments_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'proposed',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    locked_at TEXT,
    locked_by TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(source_message_id, action_type)
);

CREATE INDEX IF NOT EXISTS action_jobs_ready
ON action_jobs(status, created_at);

CREATE TABLE IF NOT EXISTS appointments (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    status TEXT NOT NULL,
    customer_name TEXT NOT NULL DEFAULT '',
    customer_phone TEXT NOT NULL DEFAULT '',
    requested_time_text TEXT NOT NULL DEFAULT '',
    confirmed_time_text TEXT NOT NULL DEFAULT '',
    proposed_reschedule_text TEXT NOT NULL DEFAULT '',
    free_reschedules_used INTEGER NOT NULL DEFAULT 0,
    payment_evidence_id TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS appointments_conversation
ON appointments(conversation_id, created_at DESC);

CREATE TABLE IF NOT EXISTS payment_evidence (
    id TEXT PRIMARY KEY,
    appointment_id TEXT NOT NULL REFERENCES appointments(id),
    source_message_id TEXT NOT NULL UNIQUE REFERENCES messages(id),
    media_storage_key TEXT NOT NULL,
    image_sha256 TEXT NOT NULL,
    extracted_amount TEXT,
    extracted_payee TEXT NOT NULL DEFAULT '',
    transaction_reference TEXT NOT NULL DEFAULT '',
    shows_success INTEGER NOT NULL DEFAULT 0,
    confidence REAL NOT NULL DEFAULT 0,
    decision TEXT NOT NULL,
    decision_reasons_json TEXT NOT NULL DEFAULT '[]',
    extractor_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS payment_evidence_image_hash
ON payment_evidence(image_sha256);

CREATE TABLE IF NOT EXISTS conversation_tasks (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    appointment_id TEXT REFERENCES appointments(id),
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    priority TEXT NOT NULL DEFAULT 'normal',
    assigned_staff_id TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS handoffs (
    id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL REFERENCES conversations(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    staff_id TEXT NOT NULL DEFAULT '',
    requested_at TEXT NOT NULL,
    activated_at TEXT,
    returned_at TEXT,
    summary TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS audit_events (
    id TEXT PRIMARY KEY,
    conversation_id TEXT REFERENCES conversations(id),
    appointment_id TEXT REFERENCES appointments(id),
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS channel_sync_state (
    channel TEXT NOT NULL,
    channel_account_id TEXT NOT NULL,
    cursor TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL,
    PRIMARY KEY(channel, channel_account_id)
);

CREATE TABLE IF NOT EXISTS wecom_callback_jobs (
    id TEXT PRIMARY KEY,
    deduplication_key TEXT NOT NULL UNIQUE,
    callback_token TEXT NOT NULL,
    open_kfid TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TEXT NOT NULL,
    locked_at TEXT,
    locked_by TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS wecom_callback_jobs_ready
ON wecom_callback_jobs(status, available_at, created_at);
"""


class SqliteMessageStore:
    """A durable local adapter; production can implement the same port in PostgreSQL."""

    def __init__(self, database_path: str | Path) -> None:
        self.database_path = str(database_path)
        Path(self.database_path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as connection:
            connection.executescript(SCHEMA)
            self._upgrade_local_schema(connection)

    def ingest(self, message: InboundMessage, debounce_seconds: float) -> ReceiveResult:
        now = _utc_now()
        conversation_id = ""
        message_id = new_id()
        with self._transaction() as connection:
            conversation = connection.execute(
                """
                SELECT id FROM conversations
                WHERE channel = ? AND channel_account_id = ? AND external_user_id = ?
                """,
                (message.channel, message.channel_account_id, message.external_user_id),
            ).fetchone()
            if conversation:
                conversation_id = str(conversation["id"])
            else:
                conversation_id = new_id()
                connection.execute(
                    """
                    INSERT INTO conversations (
                        id, channel, channel_account_id, external_user_id,
                        owner, handoff_status, last_message_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'ai', 'none', ?, ?, ?)
                    """,
                    (
                        conversation_id,
                        message.channel,
                        message.channel_account_id,
                        message.external_user_id,
                        _iso(message.occurred_at),
                        _iso(now),
                        _iso(now),
                    ),
                )

            duplicate = connection.execute(
                """
                SELECT id FROM messages
                WHERE conversation_id = ? AND external_message_id = ?
                """,
                (conversation_id, message.external_message_id),
            ).fetchone()
            if duplicate:
                return ReceiveResult(conversation_id, str(duplicate["id"]), True)

            connection.execute(
                """
                INSERT INTO messages (
                    id, conversation_id, external_message_id, actor, message_type,
                    text_content, raw_payload, occurred_at, received_at, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    conversation_id,
                    message.external_message_id,
                    message.actor.value,
                    message.message_type,
                    message.text_content,
                    json.dumps(message.raw_payload, ensure_ascii=False),
                    _iso(message.occurred_at),
                    _iso(now),
                    _iso(now),
                ),
            )
            connection.execute(
                "UPDATE conversations SET last_message_at = ?, updated_at = ? WHERE id = ?",
                (_iso(message.occurred_at), _iso(now), conversation_id),
            )
            if message.actor is MessageActor.CUSTOMER:
                available_at = now + timedelta(seconds=max(0, debounce_seconds))
                connection.execute(
                    """
                    INSERT INTO inbox_jobs (
                        id, conversation_id, message_id, status, available_at,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, 'pending', ?, ?, ?)
                    """,
                    (
                        new_id(),
                        conversation_id,
                        message_id,
                        _iso(available_at),
                        _iso(now),
                        _iso(now),
                    ),
                )
        return ReceiveResult(conversation_id, message_id, False)

    def claim_inbox_batch(self, worker_id: str, now: datetime) -> InboxBatch | None:
        with self._transaction() as connection:
            candidate = connection.execute(
                """
                SELECT j.conversation_id
                FROM inbox_jobs j
                WHERE j.status IN ('pending', 'retry')
                  AND j.available_at <= ?
                  AND NOT EXISTS (
                      SELECT 1 FROM inbox_jobs active
                      WHERE active.conversation_id = j.conversation_id
                        AND active.status = 'processing'
                  )
                ORDER BY j.available_at, j.created_at
                LIMIT 1
                """,
                (_iso(now),),
            ).fetchone()
            if not candidate:
                return None

            conversation_id = str(candidate["conversation_id"])
            jobs = connection.execute(
                """
                SELECT id, message_id, attempt_count
                FROM inbox_jobs
                WHERE conversation_id = ?
                  AND status IN ('pending', 'retry')
                  AND available_at <= ?
                ORDER BY created_at, id
                """,
                (conversation_id, _iso(now)),
            ).fetchall()
            job_ids = tuple(str(row["id"]) for row in jobs)
            if not job_ids:
                return None

            placeholders = _placeholders(job_ids)
            connection.execute(
                f"""
                UPDATE inbox_jobs
                SET status = 'processing', attempt_count = attempt_count + 1,
                    locked_at = ?, locked_by = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (_iso(now), worker_id, _iso(now), *job_ids),
            )
            message_ids = tuple(str(row["message_id"]) for row in jobs)
            message_rows = connection.execute(
                f"""
                SELECT * FROM messages
                WHERE id IN ({_placeholders(message_ids)})
                ORDER BY occurred_at, created_at, id
                """,
                message_ids,
            ).fetchall()

        messages = tuple(_row_to_message(row) for row in message_rows)
        return InboxBatch(
            conversation_id=conversation_id,
            job_ids=job_ids,
            messages=messages,
            max_attempt_count=max(int(row["attempt_count"]) + 1 for row in jobs),
        )

    def conversation_owner(self, conversation_id: str) -> ConversationOwner:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT owner FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
        if not row:
            raise LookupError(f"conversation not found: {conversation_id}")
        return ConversationOwner(str(row["owner"]))

    def channel_address(self, conversation_id: str) -> tuple[str, str, str]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT channel, channel_account_id, external_user_id
                FROM conversations WHERE id = ?
                """,
                (conversation_id,),
            ).fetchone()
        if not row:
            raise LookupError(f"conversation not found: {conversation_id}")
        return (
            str(row["channel"]),
            str(row["channel_account_id"]),
            str(row["external_user_id"]),
        )

    def sync_cursor(self, channel: str, channel_account_id: str) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT cursor FROM channel_sync_state
                WHERE channel = ? AND channel_account_id = ?
                """,
                (channel, channel_account_id),
            ).fetchone()
        return str(row["cursor"]) if row else ""

    def save_sync_cursor(
        self,
        channel: str,
        channel_account_id: str,
        cursor: str,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO channel_sync_state (
                    channel, channel_account_id, cursor, updated_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(channel, channel_account_id) DO UPDATE SET
                    cursor = excluded.cursor,
                    updated_at = excluded.updated_at
                """,
                (channel, channel_account_id, cursor, _iso(now)),
            )

    def enqueue_wecom_callback(
        self,
        deduplication_key: str,
        callback_token: str,
        open_kfid: str,
    ) -> bool:
        now = _utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO wecom_callback_jobs (
                    id, deduplication_key, callback_token, open_kfid,
                    status, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)
                """,
                (
                    new_id(),
                    deduplication_key,
                    callback_token,
                    open_kfid,
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
        return cursor.rowcount == 1

    def claim_wecom_callback(
        self,
        worker_id: str,
        now: datetime,
    ) -> WeComCallbackJob | None:
        stale_before = now - timedelta(minutes=5)
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM wecom_callback_jobs
                WHERE (status IN ('pending', 'retry') AND available_at <= ?)
                   OR (status = 'processing' AND locked_at <= ?)
                ORDER BY available_at, created_at LIMIT 1
                """,
                (_iso(now), _iso(stale_before)),
            ).fetchone()
            if not row:
                return None
            attempt_count = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE wecom_callback_jobs
                SET status = 'processing', attempt_count = ?, locked_at = ?,
                    locked_by = ?, updated_at = ? WHERE id = ?
                """,
                (attempt_count, _iso(now), worker_id, _iso(now), str(row["id"])),
            )
        return WeComCallbackJob(
            id=str(row["id"]),
            callback_token=str(row["callback_token"]),
            open_kfid=str(row["open_kfid"]),
            attempt_count=attempt_count,
        )

    def complete_wecom_callback(self, job_id: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE wecom_callback_jobs
                SET status = 'done', locked_at = NULL, locked_by = '',
                    last_error = '', updated_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (_iso(_utc_now()), job_id),
            )

    def retry_wecom_callback(
        self,
        job: WeComCallbackJob,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None:
        status = "dead" if job.attempt_count >= max_attempts else "retry"
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE wecom_callback_jobs
                SET status = ?, available_at = ?, locked_at = NULL,
                    locked_by = '', last_error = ?, updated_at = ?
                WHERE id = ? AND status = 'processing'
                """,
                (
                    status,
                    _iso(available_at),
                    error,
                    _iso(_utc_now()),
                    job.id,
                ),
            )

    def callback_job_count(self, status: str) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM wecom_callback_jobs WHERE status = ?",
                (status,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def activate_native_staff(self, conversation_id: str, staff_id: str) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT owner FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if not row:
                raise LookupError(f"conversation not found: {conversation_id}")
            if str(row["owner"]) == ConversationOwner.HUMAN.value:
                return
            connection.execute(
                """
                UPDATE conversations
                SET owner = 'human', handoff_status = 'active',
                    handoff_reason = 'native_staff_reply', assigned_staff_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), conversation_id),
            )
            updated = connection.execute(
                """
                UPDATE handoffs
                SET status = 'active', staff_id = ?, activated_at = ?
                WHERE id = (
                    SELECT id FROM handoffs
                    WHERE conversation_id = ? AND status = 'requested'
                    ORDER BY requested_at DESC LIMIT 1
                )
                """,
                (staff_id, _iso(now), conversation_id),
            )
            if updated.rowcount == 0:
                connection.execute(
                    """
                    INSERT INTO handoffs (
                        id, conversation_id, reason, status, staff_id,
                        requested_at, activated_at
                    ) VALUES (?, ?, 'native_staff_reply', 'active', ?, ?, ?)
                    """,
                    (new_id(), conversation_id, staff_id, _iso(now), _iso(now)),
                )
            connection.execute(
                """
                UPDATE conversation_tasks
                SET assigned_staff_id = ?, updated_at = ?
                WHERE conversation_id = ? AND task_type = 'take_over_conversation'
                  AND status = 'open'
                """,
                (staff_id, _iso(now), conversation_id),
            )

    def route_customer_image_to_human(self, source_message_id: str) -> None:
        self.route_customer_message_to_human(
            source_message_id,
            "customer_image_requires_staff_review",
            "图片我收到啦，这个我请店员看一下，稍后由店员接着和你沟通。",
        )

    def route_customer_message_to_human(
        self,
        source_message_id: str,
        reason: str,
        reply_text: str,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT m.conversation_id, c.owner
                FROM messages m JOIN conversations c ON c.id = m.conversation_id
                WHERE m.id = ? AND m.actor = 'customer'
                """,
                (source_message_id,),
            ).fetchone()
            if not row:
                raise ValueError("customer message was not found")
            conversation_id = str(row["conversation_id"])
            if str(row["owner"]) == ConversationOwner.AI.value:
                self._request_handoff(
                    connection,
                    conversation_id,
                    reason,
                    now,
                )
                self._insert_system_outbox(
                    connection,
                    conversation_id,
                    f"media-handoff:{source_message_id}",
                    "text",
                    {"text": reply_text},
                    now,
                )
            connection.execute(
                """
                UPDATE inbox_jobs SET status = 'done', updated_at = ?
                WHERE message_id = ? AND status IN ('pending', 'retry')
                """,
                (_iso(now), source_message_id),
            )

    def set_conversation_owner(
        self,
        conversation_id: str,
        owner: ConversationOwner,
        handoff_status: str,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE conversations
                SET owner = ?, handoff_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (owner.value, handoff_status, _iso(now), conversation_id),
            )
            if cursor.rowcount != 1:
                raise LookupError(f"conversation not found: {conversation_id}")

    def complete_inbox_batch(self, batch: InboxBatch, reply_text: str | None) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            if reply_text and reply_text.strip():
                first_id = batch.messages[0].id
                last_id = batch.messages[-1].id
                outbox_id = new_id()
                idempotency_key = f"reply:{first_id}:{last_id}"
                payload = json.dumps({"text": reply_text.strip()}, ensure_ascii=False)
                connection.execute(
                    """
                    INSERT OR IGNORE INTO outbox_messages (
                        id, conversation_id, actor, idempotency_key, message_type,
                        payload, status, available_at, created_at, updated_at
                    ) VALUES (?, ?, 'ai', ?, 'text', ?, 'pending', ?, ?, ?)
                    """,
                    (
                        outbox_id,
                        batch.conversation_id,
                        idempotency_key,
                        payload,
                        _iso(now),
                        _iso(now),
                        _iso(now),
                    ),
                )
            self._finish_inbox_jobs(connection, batch.job_ids, "done", "", now)

    def complete_dialogue_batch(
        self,
        batch: InboxBatch,
        decision: DialogueDecision,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            memory = self._load_memory(connection, batch.conversation_id)
            facts = {fact.key: fact for fact in memory.facts}
            for update in decision.memory_updates:
                facts[update.key] = update
            action_cursor = connection.execute(
                """
                INSERT INTO conversation_memory (
                    conversation_id, summary, facts_json, pending_tasks_json,
                    summary_through_message_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    summary = excluded.summary,
                    facts_json = excluded.facts_json,
                    pending_tasks_json = excluded.pending_tasks_json,
                    summary_through_message_id = excluded.summary_through_message_id,
                    updated_at = excluded.updated_at
                """,
                (
                    batch.conversation_id,
                    memory.summary,
                    _facts_to_json(tuple(facts.values())),
                    json.dumps(memory.pending_tasks, ensure_ascii=False),
                    memory.summary_through_message_id,
                    _iso(now),
                ),
            )

            source_message_id = batch.messages[-1].id
            for signal in set(decision.catalog_signals):
                connection.execute(
                    """
                    INSERT OR IGNORE INTO catalog_inventory_events (
                        id, event_type, model_code, unit_code, source, notes, occurred_at
                    ) VALUES (?, ?, ?, NULL, 'ai_dialogue', ?, ?)
                    """,
                    (
                        f"dialogue:{source_message_id}:{signal.event_type}:{signal.catalog_ref}",
                        signal.event_type,
                        signal.catalog_ref,
                        f"source_message_id={source_message_id}",
                        _iso(now),
                    ),
                )
            for action in decision.proposed_actions:
                is_handoff = action.type is ActionType.HANDOFF_TO_HUMAN
                action_status = "done" if is_handoff else "proposed"
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO action_jobs (
                        id, conversation_id, source_message_id, action_type,
                        arguments_json, status, available_at, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        batch.conversation_id,
                        source_message_id,
                        action.type.value,
                        json.dumps(action.arguments, ensure_ascii=False),
                        action_status,
                        _iso(now),
                        _iso(now),
                        _iso(now),
                    ),
                )
                if is_handoff and cursor.rowcount == 1:
                    self._request_handoff(
                        connection,
                        batch.conversation_id,
                        str(action.arguments["reason"]),
                        now,
                    )

            reply_text = decision.reply
            if not decision.needs_human:
                recommended_refs = tuple(
                    signal.catalog_ref
                    for signal in decision.catalog_signals
                    if signal.event_type == "recommended"
                )
                queued_images = self._insert_catalog_recommendation_outboxes(
                    connection,
                    batch.conversation_id,
                    source_message_id,
                    recommended_refs,
                    now,
                )
                if not recommended_refs and _catalog_image_resend_requested(batch.messages):
                    queued_images += self._insert_catalog_resend_outbox(
                        connection,
                        batch.conversation_id,
                        source_message_id,
                        now,
                    )
                if _catalog_image_send_intent(reply_text) and queued_images == 0:
                    reply_text = (
                        "我还没找到可以直接发送的对应图片，先不乱发。"
                        "你更想看哪种领型、袖型或面料？"
                    )
            self._insert_reply_outbox(
                connection,
                batch,
                reply_text,
                now,
                "reply",
                actor="system" if decision.needs_human else "ai",
            )
            self._finish_inbox_jobs(connection, batch.job_ids, "done", "", now)

    def retry_inbox_batch(
        self,
        batch: InboxBatch,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None:
        status = "dead" if batch.max_attempt_count >= max_attempts else "retry"
        now = _utc_now()
        with self._transaction() as connection:
            placeholders = _placeholders(batch.job_ids)
            connection.execute(
                f"""
                UPDATE inbox_jobs
                SET status = ?, available_at = ?, locked_at = NULL, locked_by = '',
                    last_error = ?, updated_at = ?
                WHERE id IN ({placeholders})
                """,
                (status, _iso(available_at), error, _iso(now), *batch.job_ids),
            )

    def terminal_inbox_failure(
        self,
        batch: InboxBatch,
        error: str,
        fallback_reply: str,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            first_id = batch.messages[0].id
            last_id = batch.messages[-1].id
            connection.execute(
                """
                INSERT OR IGNORE INTO outbox_messages (
                    id, conversation_id, actor, idempotency_key, message_type,
                    payload, status, available_at, created_at, updated_at
                ) VALUES (?, ?, 'system', ?, 'text', ?, 'pending', ?, ?, ?)
                """,
                (
                    new_id(),
                    batch.conversation_id,
                    f"terminal-fallback:{first_id}:{last_id}",
                    json.dumps({"text": fallback_reply}, ensure_ascii=False),
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
            action_cursor = connection.execute(
                """
                INSERT OR IGNORE INTO action_jobs (
                    id, conversation_id, source_message_id, action_type,
                    arguments_json, status, available_at, created_at, updated_at
                ) VALUES (?, ?, ?, 'handoff_to_human', ?, 'done', ?, ?, ?)
                """,
                (
                    new_id(),
                    batch.conversation_id,
                    last_id,
                    json.dumps(
                        {"reason": "dialogue_engine_failed", "error": error},
                        ensure_ascii=False,
                    ),
                    _iso(now),
                    _iso(now),
                    _iso(now),
                ),
            )
            if action_cursor.rowcount == 1:
                self._request_handoff(
                    connection,
                    batch.conversation_id,
                    "dialogue_engine_failed",
                    now,
                )
            self._finish_inbox_jobs(connection, batch.job_ids, "dead", error, now)

    def claim_outbox(self, worker_id: str, now: datetime) -> OutboxMessage | None:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM outbox_messages
                WHERE status IN ('pending', 'retry') AND available_at <= ?
                ORDER BY available_at, created_at
                LIMIT 1
                """,
                (_iso(now),),
            ).fetchone()
            if not row:
                return None
            connection.execute(
                """
                UPDATE outbox_messages
                SET status = 'sending', attempt_count = attempt_count + 1,
                    locked_at = ?, locked_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (_iso(now), worker_id, _iso(now), str(row["id"])),
            )
        return OutboxMessage(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            idempotency_key=str(row["idempotency_key"]),
            actor=MessageActor(str(row["actor"])),
            message_type=str(row["message_type"]),
            payload=json.loads(str(row["payload"])),
            attempt_count=int(row["attempt_count"]) + 1,
        )

    def mark_outbox_sent(
        self,
        message: OutboxMessage,
        external_message_id: str,
        sent_at: datetime,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE outbox_messages
                SET status = 'sent', external_message_id = ?, sent_at = ?,
                    locked_at = NULL, locked_by = '', last_error = '', updated_at = ?
                WHERE id = ?
                """,
                (
                    external_message_id,
                    _iso(sent_at),
                    _iso(now),
                    message.id,
                ),
            )
            if message.message_type in {"text", "image"}:
                text_content = str(message.payload.get("text") or "")
                if message.message_type == "image":
                    caption = str(message.payload.get("caption") or "图片")
                    text_content = f"[已发送{caption}]"
                connection.execute(
                    """
                    INSERT OR IGNORE INTO messages (
                        id, conversation_id, external_message_id, actor,
                        message_type, text_content, raw_payload, occurred_at,
                        received_at, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        new_id(),
                        message.conversation_id,
                        external_message_id or f"outbox:{message.id}",
                        message.actor.value,
                        message.message_type,
                        text_content,
                        json.dumps({"outbox_id": message.id}, ensure_ascii=False),
                        _iso(sent_at),
                        _iso(sent_at),
                        _iso(now),
                    ),
                )
                connection.execute(
                    """
                    UPDATE conversations
                    SET last_message_at = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (_iso(sent_at), _iso(now), message.conversation_id),
                )

    def retry_outbox(
        self,
        message: OutboxMessage,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None:
        status = "dead" if message.attempt_count >= max_attempts else "retry"
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE outbox_messages
                SET status = ?, available_at = ?, locked_at = NULL, locked_by = '',
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    _iso(available_at),
                    error,
                    _iso(_utc_now()),
                    message.id,
                ),
            )

    def cancel_outbox(self, message: OutboxMessage, reason: str) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE outbox_messages
                SET status = 'cancelled', locked_at = NULL, locked_by = '',
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (reason, _iso(_utc_now()), message.id),
            )

    def recover_stale(self, stale_before: datetime, now: datetime) -> tuple[int, int]:
        with self._transaction() as connection:
            inbox = connection.execute(
                """
                UPDATE inbox_jobs
                SET status = 'retry', available_at = ?, locked_at = NULL,
                    locked_by = '', last_error = 'stale worker recovered', updated_at = ?
                WHERE status = 'processing' AND locked_at < ?
                """,
                (_iso(now), _iso(now), _iso(stale_before)),
            ).rowcount
            outbox = connection.execute(
                """
                UPDATE outbox_messages
                SET status = 'retry', available_at = ?, locked_at = NULL,
                    locked_by = '', last_error = 'stale sender recovered', updated_at = ?
                WHERE status = 'sending' AND locked_at < ?
                """,
                (_iso(now), _iso(now), _iso(stale_before)),
            ).rowcount
        return inbox, outbox

    def status_counts(self, table: str) -> dict[str, int]:
        if table not in {"inbox_jobs", "outbox_messages"}:
            raise ValueError("unsupported status table")
        with closing(self._connect()) as connection:
            rows = connection.execute(
                f"SELECT status, COUNT(*) AS count FROM {table} GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}

    def recent_messages(
        self,
        conversation_id: str,
        limit: int = 20,
    ) -> tuple[StoredMessage, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM (
                    SELECT * FROM messages
                    WHERE conversation_id = ?
                    ORDER BY occurred_at DESC, created_at DESC, id DESC
                    LIMIT ?
                ) recent
                ORDER BY occurred_at, created_at, id
                """,
                (conversation_id, limit),
            ).fetchall()
        return tuple(_row_to_message(row) for row in rows)

    def load_memory(self, conversation_id: str) -> ConversationMemory:
        with closing(self._connect()) as connection:
            return self._load_memory(connection, conversation_id)

    def action_count(self, status: str = "proposed") -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM action_jobs WHERE status = ?",
                (status,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def claim_action(self, worker_id: str, now: datetime) -> ActionJob | None:
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM action_jobs
                WHERE status IN ('proposed', 'retry') AND available_at <= ?
                ORDER BY available_at, created_at
                LIMIT 1
                """,
                (_iso(now),),
            ).fetchone()
            if not row:
                return None
            attempt_count = int(row["attempt_count"]) + 1
            connection.execute(
                """
                UPDATE action_jobs
                SET status = 'executing', attempt_count = ?, locked_at = ?,
                    locked_by = ?, updated_at = ?
                WHERE id = ?
                """,
                (attempt_count, _iso(now), worker_id, _iso(now), str(row["id"])),
            )
        return ActionJob(
            id=str(row["id"]),
            conversation_id=str(row["conversation_id"]),
            source_message_id=str(row["source_message_id"]),
            action_type=ActionType(str(row["action_type"])),
            arguments=json.loads(str(row["arguments_json"])),
            attempt_count=attempt_count,
        )

    def execute_time_confirmation_request(self, job: ActionJob) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            self._require_executing_action(connection, job)
            memory = self._load_memory(connection, job.conversation_id)
            facts = {fact.key: fact.value for fact in memory.facts}
            customer_name = str(facts.get("customer_name") or "").strip()
            customer_phone = str(facts.get("customer_phone") or "").strip()
            if not customer_name:
                raise ValueError("customer name is required before requesting staff confirmation")
            if not customer_phone:
                raise ValueError("customer phone is required before requesting staff confirmation")

            requested_time = str(job.arguments.get("requested_time") or "").strip()
            requested_date = str(job.arguments.get("requested_date") or "").strip()
            if not requested_time:
                raise ValueError("requested time is required")
            requested_text = " ".join(item for item in (requested_date, requested_time) if item)

            active = connection.execute(
                """
                SELECT id FROM appointments
                WHERE conversation_id = ?
                  AND status NOT IN ('cancelled', 'completed')
                LIMIT 1
                """,
                (job.conversation_id,),
            ).fetchone()
            if active:
                raise ValueError("conversation already has an active appointment")

            appointment_id = new_id()
            connection.execute(
                """
                INSERT INTO appointments (
                    id, conversation_id, status, customer_name, customer_phone,
                    requested_time_text, created_at, updated_at
                ) VALUES (?, ?, 'time_pending_staff', ?, ?, ?, ?, ?)
                """,
                (
                    appointment_id,
                    job.conversation_id,
                    customer_name,
                    customer_phone,
                    requested_text,
                    _iso(now),
                    _iso(now),
                ),
            )
            task_id = new_id()
            connection.execute(
                """
                INSERT INTO conversation_tasks (
                    id, conversation_id, appointment_id, task_type, status,
                    priority, details_json, created_at, updated_at
                ) VALUES (?, ?, ?, 'confirm_appointment_time', 'open',
                          'normal', ?, ?, ?)
                """,
                (
                    task_id,
                    job.conversation_id,
                    appointment_id,
                    json.dumps(
                        {
                            "requested_time": requested_text,
                            "customer_name": customer_name,
                            "customer_phone": customer_phone,
                        },
                        ensure_ascii=False,
                    ),
                    _iso(now),
                    _iso(now),
                ),
            )
            self._insert_system_outbox(
                connection,
                job.conversation_id,
                f"staff-time-confirmation:{task_id}",
                "staff_textcard",
                {
                    "task_id": task_id,
                    "title": "新的试纱时间待确认",
                    "description": (
                        f"{customer_name}希望预约 {requested_text}，请确认是否可以安排。"
                    ),
                },
                now,
            )
            self._finish_action(connection, job.id, "done", "", now)

    def reject_action(self, job: ActionJob, reason: str) -> None:
        with self._transaction() as connection:
            self._require_executing_action(connection, job)
            self._finish_action(connection, job.id, "rejected", reason, _utc_now())

    def retry_action(
        self,
        job: ActionJob,
        error: str,
        available_at: datetime,
        max_attempts: int,
    ) -> None:
        status = "dead" if job.attempt_count >= max_attempts else "retry"
        with self._transaction() as connection:
            self._require_executing_action(connection, job)
            connection.execute(
                """
                UPDATE action_jobs
                SET status = ?, available_at = ?, locked_at = NULL, locked_by = '',
                    last_error = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    status,
                    _iso(available_at),
                    error,
                    _iso(_utc_now()),
                    job.id,
                ),
            )

    def load_appointment_state(self, conversation_id: str) -> dict[str, object]:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT * FROM appointments
                WHERE conversation_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
        if not row:
            return {}
        return {
            "id": str(row["id"]),
            "status": str(row["status"]),
            "requested_time": str(row["requested_time_text"]),
            "confirmed_time": str(row["confirmed_time_text"]),
            "staff_time_approved": str(row["status"])
            in {"awaiting_payment", "payment_review", "confirmed"},
            "has_payment_evidence": bool(str(row["payment_evidence_id"])),
        }

    def task_count(self, task_type: str, status: str = "open") -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT COUNT(*) AS count FROM conversation_tasks
                WHERE task_type = ? AND status = ?
                """,
                (task_type, status),
            ).fetchone()
        return int(row["count"]) if row else 0

    def first_open_task_id(self, task_type: str) -> str:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT id FROM conversation_tasks
                WHERE task_type = ? AND status = 'open'
                ORDER BY created_at LIMIT 1
                """,
                (task_type,),
            ).fetchone()
        return str(row["id"]) if row else ""

    def audit_count(self, event_type: str) -> int:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT COUNT(*) AS count FROM audit_events WHERE event_type = ?",
                (event_type,),
            ).fetchone()
        return int(row["count"]) if row else 0

    def payment_evidence_decision(
        self,
        source_message_id: str,
    ) -> PaymentDecision | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT decision FROM payment_evidence WHERE source_message_id = ?",
                (source_message_id,),
            ).fetchone()
        return PaymentDecision(str(row["decision"])) if row else None

    def apply_payment_evaluation(
        self,
        evidence: PaymentEvidence,
        evaluation: PaymentEvaluation,
        image_sha256: str,
        media_storage_key: str,
    ) -> PaymentDecision:
        now = _utc_now()
        with self._transaction() as connection:
            existing = connection.execute(
                "SELECT decision FROM payment_evidence WHERE source_message_id = ?",
                (evidence.source_message_id,),
            ).fetchone()
            if existing:
                return PaymentDecision(str(existing["decision"]))

            message = connection.execute(
                "SELECT conversation_id FROM messages WHERE id = ?",
                (evidence.source_message_id,),
            ).fetchone()
            if not message:
                raise ValueError("payment source message was not found")
            conversation_id = str(message["conversation_id"])
            appointment = connection.execute(
                """
                SELECT * FROM appointments
                WHERE conversation_id = ?
                  AND status IN ('awaiting_payment', 'payment_review')
                ORDER BY created_at DESC LIMIT 1
                """,
                (conversation_id,),
            ).fetchone()
            if not appointment:
                raise ValueError("appointment is not awaiting payment")

            reasons = list(evaluation.reasons)
            duplicate = connection.execute(
                "SELECT id FROM payment_evidence WHERE image_sha256 = ? LIMIT 1",
                (image_sha256,),
            ).fetchone()
            if duplicate:
                reasons.append("duplicate_image")
            decision = PaymentDecision.REVIEW if reasons else evaluation.decision
            appointment_id = str(appointment["id"])
            connection.execute(
                """
                INSERT INTO payment_evidence (
                    id, appointment_id, source_message_id, media_storage_key,
                    image_sha256, extracted_amount, extracted_payee,
                    transaction_reference, shows_success, confidence, decision,
                    decision_reasons_json, extractor_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ocr-v1', ?)
                """,
                (
                    evidence.id,
                    appointment_id,
                    evidence.source_message_id,
                    media_storage_key,
                    image_sha256,
                    str(evidence.amount) if evidence.amount is not None else None,
                    evidence.payee,
                    evidence.transaction_reference,
                    int(evidence.shows_success),
                    evidence.confidence,
                    decision.value,
                    json.dumps(reasons, ensure_ascii=False),
                    _iso(now),
                ),
            )

            if decision is PaymentDecision.ACCEPT:
                connection.execute(
                    """
                    UPDATE appointments
                    SET status = 'confirmed', payment_evidence_id = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (evidence.id, _iso(now), appointment_id),
                )
                self._insert_system_outbox(
                    connection,
                    conversation_id,
                    f"payment-confirmed:{evidence.id}",
                    "text",
                    {
                        "text": (
                            f"收到付款啦，已经为你锁定 {appointment['confirmed_time_text']} 的试纱档期。"
                            "到店前如果时间有变化，提前告诉我就好。"
                        )
                    },
                    now,
                )
                event_type = "payment_evidence_accepted"
            else:
                task_id = new_id()
                connection.execute(
                    "UPDATE appointments SET status = 'payment_review', updated_at = ? WHERE id = ?",
                    (_iso(now), appointment_id),
                )
                connection.execute(
                    """
                    INSERT INTO conversation_tasks (
                        id, conversation_id, appointment_id, task_type, status,
                        priority, details_json, created_at, updated_at
                    ) VALUES (?, ?, ?, 'review_payment', 'open', ?, ?, ?, ?)
                    """,
                    (
                        task_id,
                        conversation_id,
                        appointment_id,
                        "urgent" if "duplicate_image" in reasons else "normal",
                        json.dumps(
                            {"evidence_id": evidence.id, "reasons": reasons},
                            ensure_ascii=False,
                        ),
                        _iso(now),
                        _iso(now),
                    ),
                )
                self._insert_system_outbox(
                    connection,
                    conversation_id,
                    f"staff-payment-review:{task_id}",
                    "staff_textcard",
                    {
                        "task_id": task_id,
                        "title": "付款截图待复核",
                        "description": (
                            f"{appointment['customer_name']}的付款截图未通过自动核对，"
                            "请查看原图并确认处理结果。"
                        ),
                    },
                    now,
                )
                self._insert_system_outbox(
                    connection,
                    conversation_id,
                    f"payment-review:{evidence.id}",
                    "text",
                    {"text": _payment_review_message(reasons, evidence.amount)},
                    now,
                )
                event_type = "payment_evidence_review_required"

            self._insert_audit_event(
                connection,
                conversation_id,
                appointment_id,
                "system",
                "payment-verifier",
                event_type,
                {"evidence_id": evidence.id, "reasons": reasons},
                now,
            )
            connection.execute(
                """
                UPDATE inbox_jobs
                SET status = 'done', locked_at = NULL, locked_by = '',
                    last_error = '', updated_at = ?
                WHERE message_id = ? AND status IN ('pending', 'retry', 'processing')
                """,
                (_iso(now), evidence.source_message_id),
            )
            return decision

    def load_payment_task_view(self, task_id: str) -> StaffPaymentTask:
        with closing(self._connect()) as connection:
            task, appointment, evidence = self._load_payment_task(connection, task_id)
        return StaffPaymentTask(
            id=str(task["id"]),
            status=str(task["status"]),
            appointment_status=str(appointment["status"]),
            customer_name=str(appointment["customer_name"]),
            confirmed_time=str(appointment["confirmed_time_text"]),
            extracted_amount=str(evidence["extracted_amount"] or ""),
            extracted_payee=str(evidence["extracted_payee"] or ""),
            shows_success=bool(evidence["shows_success"]),
            confidence=float(evidence["confidence"]),
            reasons=tuple(json.loads(str(evidence["decision_reasons_json"]))),
            evidence_path=Path(str(evidence["media_storage_key"])),
        )

    def approve_payment_task(self, task_id: str, staff_id: str) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            task, appointment, evidence = self._load_payment_task(connection, task_id)
            if str(task["status"]) == "resolved":
                if str(appointment["status"]) == "confirmed":
                    return
                raise ValueError("payment review task is already resolved")
            if str(task["status"]) != "open" or str(appointment["status"]) != "payment_review":
                raise ValueError("payment is not waiting for staff review")
            connection.execute(
                """
                UPDATE appointments
                SET status = 'confirmed', payment_evidence_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (str(evidence["id"]), _iso(now), str(appointment["id"])),
            )
            connection.execute(
                """
                UPDATE conversation_tasks
                SET status = 'resolved', assigned_staff_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), task_id),
            )
            self._insert_system_outbox(
                connection,
                str(appointment["conversation_id"]),
                f"payment-review-approved:{task_id}",
                "text",
                {
                    "text": (
                        f"店员已经核对好啦，{_customer_time_text(str(appointment['confirmed_time_text']))}"
                        "的试纱档期已经锁定。到店前如果时间有变化，提前告诉我就好。"
                    )
                },
                now,
            )
            self._insert_audit_event(
                connection,
                str(appointment["conversation_id"]),
                str(appointment["id"]),
                "staff",
                staff_id,
                "payment_review_approved",
                {"evidence_id": str(evidence["id"])},
                now,
            )

    def reject_payment_task(self, task_id: str, staff_id: str) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            task, appointment, evidence = self._load_payment_task(connection, task_id)
            if str(task["status"]) == "resolved":
                if str(appointment["status"]) == "awaiting_payment":
                    return
                raise ValueError("payment review task is already resolved")
            if str(task["status"]) != "open" or str(appointment["status"]) != "payment_review":
                raise ValueError("payment is not waiting for staff review")
            connection.execute(
                "UPDATE appointments SET status = 'awaiting_payment', updated_at = ? WHERE id = ?",
                (_iso(now), str(appointment["id"])),
            )
            connection.execute(
                """
                UPDATE conversation_tasks
                SET status = 'resolved', assigned_staff_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), task_id),
            )
            self._insert_system_outbox(
                connection,
                str(appointment["conversation_id"]),
                f"payment-review-rejected:{task_id}",
                "text",
                {
                    "text": (
                        "店员核对后，这张截图暂时不能作为本次试纱付款凭证，档期还没有锁定。"
                        "麻烦重新发送完整的支付成功页面，金额、收款方和支付状态都要清楚显示。"
                    )
                },
                now,
            )
            self._insert_audit_event(
                connection,
                str(appointment["conversation_id"]),
                str(appointment["id"]),
                "staff",
                staff_id,
                "payment_review_rejected",
                {"evidence_id": str(evidence["id"])},
                now,
            )

    def load_handoff_task_view(self, task_id: str) -> StaffHandoffTask:
        with closing(self._connect()) as connection:
            task, conversation = self._load_handoff_task(connection, task_id)
            appointment = connection.execute(
                """
                SELECT customer_name, customer_phone, status, confirmed_time_text,
                       requested_time_text
                FROM appointments
                WHERE conversation_id = ?
                ORDER BY created_at DESC LIMIT 1
                """,
                (str(conversation["id"]),),
            ).fetchone()
            rows = connection.execute(
                """
                SELECT actor, message_type, text_content
                FROM messages
                WHERE conversation_id = ?
                ORDER BY occurred_at DESC, id DESC
                """,
                (str(conversation["id"]),),
            ).fetchall()
        messages = tuple(
            StaffHandoffMessage(
                actor=str(row["actor"]),
                text=(str(row["text_content"]).strip() or f"[{row['message_type']}消息]"),
            )
            for row in reversed(rows)
        )
        return StaffHandoffTask(
            id=str(task["id"]),
            status=str(task["status"]),
            owner=str(conversation["owner"]),
            handoff_status=str(conversation["handoff_status"]),
            reason=str(conversation["handoff_reason"]),
            customer_name=str(appointment["customer_name"] if appointment else ""),
            customer_phone=str(appointment["customer_phone"] if appointment else ""),
            appointment_status=str(appointment["status"] if appointment else ""),
            appointment_time=str(
                (appointment["confirmed_time_text"] or appointment["requested_time_text"])
                if appointment
                else ""
            ),
            messages=messages,
            conversation_id=str(conversation["id"]),
        )

    def active_handoff_task_ids_for_staff(self, staff_id: str) -> tuple[str, ...]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT t.id
                FROM conversation_tasks t
                JOIN conversations c ON c.id = t.conversation_id
                WHERE t.task_type = 'take_over_conversation' AND t.status = 'open'
                  AND c.owner = 'human' AND c.handoff_status = 'active'
                  AND c.assigned_staff_id = ?
                ORDER BY c.updated_at DESC, t.created_at DESC
                """,
                (staff_id.strip(),),
            ).fetchall()
        return tuple(str(row["id"]) for row in rows)

    def activate_handoff_task(self, task_id: str, staff_id: str) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            task, conversation = self._load_handoff_task(connection, task_id)
            if str(task["status"]) != "open":
                raise ValueError("人工接管任务已经结束")
            owner = str(conversation["owner"])
            if owner == ConversationOwner.HUMAN.value:
                return
            if owner != ConversationOwner.HUMAN_PENDING.value:
                raise ValueError("会话当前不在等待人工接管")
            conversation_id = str(conversation["id"])
            connection.execute(
                """
                UPDATE conversations
                SET owner = 'human', handoff_status = 'active', assigned_staff_id = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), conversation_id),
            )
            connection.execute(
                """
                UPDATE handoffs
                SET status = 'active', staff_id = ?, activated_at = ?
                WHERE id = (
                    SELECT id FROM handoffs
                    WHERE conversation_id = ? AND status = 'requested'
                    ORDER BY requested_at DESC LIMIT 1
                )
                """,
                (staff_id, _iso(now), conversation_id),
            )
            connection.execute(
                """
                UPDATE conversation_tasks
                SET assigned_staff_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), task_id),
            )
            self._insert_audit_event(
                connection,
                conversation_id,
                None,
                "staff",
                staff_id,
                "handoff_activated",
                {"task_id": task_id},
                now,
            )

    def return_handoff_task(
        self,
        task_id: str,
        staff_id: str,
        summary: str,
    ) -> None:
        summary = summary.strip()
        if not summary:
            raise ValueError("交接摘要不能为空")
        now = _utc_now()
        with self._transaction() as connection:
            task, conversation = self._load_handoff_task(connection, task_id)
            if str(task["status"]) == "resolved" and str(conversation["owner"]) == "ai":
                return
            if str(task["status"]) != "open" or str(conversation["owner"]) != "human":
                raise ValueError("会话当前不在人工接管中")
            conversation_id = str(conversation["id"])
            connection.execute(
                """
                UPDATE conversations
                SET owner = 'ai', handoff_status = 'none', handoff_reason = '',
                    assigned_staff_id = '', handoff_summary = ?, updated_at = ?
                WHERE id = ?
                """,
                (summary, _iso(now), conversation_id),
            )
            connection.execute(
                """
                UPDATE handoffs
                SET status = 'returned', returned_at = ?, summary = ?
                WHERE id = (
                    SELECT id FROM handoffs
                    WHERE conversation_id = ? AND status = 'active'
                    ORDER BY requested_at DESC LIMIT 1
                )
                """,
                (_iso(now), summary, conversation_id),
            )
            connection.execute(
                """
                UPDATE conversation_tasks
                SET status = 'resolved', assigned_staff_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), task_id),
            )
            existing = connection.execute(
                "SELECT summary FROM conversation_memory WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
            prior_summary = str(existing["summary"] if existing else "").strip()
            merged_summary = "\n".join(
                item for item in (prior_summary, f"人工交接：{summary}") if item
            )[-2000:]
            connection.execute(
                """
                INSERT INTO conversation_memory (
                    conversation_id, summary, facts_json, pending_tasks_json,
                    summary_through_message_id, updated_at
                ) VALUES (?, ?, '[]', '[]', '', ?)
                ON CONFLICT(conversation_id) DO UPDATE SET
                    summary = excluded.summary,
                    updated_at = excluded.updated_at
                """,
                (conversation_id, merged_summary, _iso(now)),
            )
            self._insert_audit_event(
                connection,
                conversation_id,
                None,
                "staff",
                staff_id,
                "handoff_returned_to_ai",
                {"task_id": task_id},
                now,
            )

    def queue_handoff_reply(self, task_id: str, staff_id: str, text: str) -> None:
        text = text.strip()
        if not text:
            raise ValueError("回复内容不能为空")
        now = _utc_now()
        with self._transaction() as connection:
            task, conversation = self._load_handoff_task(connection, task_id)
            if str(task["status"]) != "open" or str(conversation["owner"]) != "human":
                raise ValueError("请先接管会话后再回复顾客")
            reply_id = new_id()
            self._insert_system_outbox(
                connection,
                str(conversation["id"]),
                f"human-reply:{task_id}:{reply_id}",
                "text",
                {"text": text},
                now,
            )
            connection.execute(
                """
                UPDATE outbox_messages
                SET actor = 'staff'
                WHERE idempotency_key = ?
                """,
                (f"human-reply:{task_id}:{reply_id}",),
            )
            self._insert_audit_event(
                connection,
                str(conversation["id"]),
                None,
                "staff",
                staff_id,
                "human_reply_queued",
                {"task_id": task_id, "character_count": len(text)},
                now,
            )

    def load_time_task_view(self, task_id: str) -> StaffTimeTask:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT t.id, t.status AS task_status,
                       a.status AS appointment_status, a.customer_name,
                       a.customer_phone, a.requested_time_text
                FROM conversation_tasks t
                JOIN appointments a ON a.id = t.appointment_id
                WHERE t.id = ? AND t.task_type = 'confirm_appointment_time'
                """,
                (task_id,),
            ).fetchone()
        if not row:
            raise ValueError("time confirmation task was not found")
        return StaffTimeTask(
            id=str(row["id"]),
            status=str(row["task_status"]),
            appointment_status=str(row["appointment_status"]),
            customer_name=str(row["customer_name"]),
            customer_phone=str(row["customer_phone"]),
            requested_time=str(row["requested_time_text"]),
        )

    def approve_time_task(
        self,
        task_id: str,
        staff_id: str,
        payment_codes: PaymentCodes,
    ) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = self._load_time_task(connection, task_id)
            if str(row["task_status"]) == "resolved":
                if str(row["appointment_status"]) == "awaiting_payment":
                    return
                raise ValueError("time confirmation task is already resolved")
            if str(row["task_status"]) != "open":
                raise ValueError("time confirmation task is not open")
            if str(row["appointment_status"]) != "time_pending_staff":
                raise ValueError("appointment is not waiting for staff confirmation")

            appointment_id = str(row["appointment_id"])
            conversation_id = str(row["conversation_id"])
            requested_time = str(row["requested_time_text"])
            connection.execute(
                """
                UPDATE appointments
                SET status = 'awaiting_payment', confirmed_time_text = ?, updated_at = ?
                WHERE id = ?
                """,
                (requested_time, _iso(now), appointment_id),
            )
            connection.execute(
                """
                UPDATE conversation_tasks
                SET status = 'resolved', assigned_staff_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), task_id),
            )
            self._cancel_pending_staff_notification(connection, task_id, now)

            intro = (
                f"时间确认好啦，{_customer_time_text(requested_time)}可以安排～\n"
                "试纱费200元，最多试6件，时间大约2小时。下面是微信和支付宝收款码，"
                "付款后把截图发我，核对通过就可以锁定档期啦。"
            )
            self._insert_system_outbox(
                connection,
                conversation_id,
                f"appointment-payment:{appointment_id}:intro",
                "text",
                {"text": intro},
                now,
            )
            self._insert_system_outbox(
                connection,
                conversation_id,
                f"appointment-payment:{appointment_id}:wechat",
                "image",
                {"path": str(payment_codes.wechat), "caption": "微信收款码"},
                now + timedelta(milliseconds=1),
            )
            self._insert_system_outbox(
                connection,
                conversation_id,
                f"appointment-payment:{appointment_id}:alipay",
                "image",
                {"path": str(payment_codes.alipay), "caption": "支付宝收款码"},
                now + timedelta(milliseconds=2),
            )
            self._insert_audit_event(
                connection,
                conversation_id,
                appointment_id,
                "staff",
                staff_id,
                "appointment_time_approved",
                {"confirmed_time": requested_time},
                now,
            )

    def reject_time_task(self, task_id: str, staff_id: str) -> None:
        now = _utc_now()
        with self._transaction() as connection:
            row = self._load_time_task(connection, task_id)
            if str(row["task_status"]) == "resolved":
                if str(row["appointment_status"]) == "collecting_details":
                    return
                raise ValueError("time confirmation task is already resolved")
            if str(row["task_status"]) != "open":
                raise ValueError("time confirmation task is not open")
            if str(row["appointment_status"]) != "time_pending_staff":
                raise ValueError("appointment is not waiting for staff confirmation")

            appointment_id = str(row["appointment_id"])
            conversation_id = str(row["conversation_id"])
            connection.execute(
                """
                UPDATE appointments
                SET status = 'collecting_details', requested_time_text = '', updated_at = ?
                WHERE id = ?
                """,
                (_iso(now), appointment_id),
            )
            connection.execute(
                """
                UPDATE conversation_tasks
                SET status = 'resolved', assigned_staff_id = ?, updated_at = ?
                WHERE id = ?
                """,
                (staff_id, _iso(now), task_id),
            )
            self._cancel_pending_staff_notification(connection, task_id, now)
            self._insert_system_outbox(
                connection,
                conversation_id,
                f"appointment-time-rejected:{appointment_id}",
                "text",
                {
                    "text": (
                        "这个时间店员刚确认过，暂时安排不了。你还有其他方便的日期或时间吗？"
                        "我再帮你确认。"
                    )
                },
                now,
            )
            self._insert_audit_event(
                connection,
                conversation_id,
                appointment_id,
                "staff",
                staff_id,
                "appointment_time_rejected",
                {},
                now,
            )

    @staticmethod
    def _cancel_pending_staff_notification(
        connection: sqlite3.Connection,
        task_id: str,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE outbox_messages
            SET status = 'cancelled', last_error = 'staff task already resolved',
                locked_at = NULL, locked_by = '', updated_at = ?
            WHERE idempotency_key = ? AND status IN ('pending', 'retry')
            """,
            (_iso(now), f"staff-time-confirmation:{task_id}"),
        )

    @staticmethod
    def _load_time_task(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT t.status AS task_status, t.conversation_id,
                   a.id AS appointment_id, a.status AS appointment_status,
                   a.requested_time_text
            FROM conversation_tasks t
            JOIN appointments a ON a.id = t.appointment_id
            WHERE t.id = ? AND t.task_type = 'confirm_appointment_time'
            """,
            (task_id,),
        ).fetchone()
        if not row:
            raise ValueError("time confirmation task was not found")
        return row

    @staticmethod
    def _load_payment_task(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row, sqlite3.Row]:
        task = connection.execute(
            """
            SELECT * FROM conversation_tasks
            WHERE id = ? AND task_type = 'review_payment'
            """,
            (task_id,),
        ).fetchone()
        if not task:
            raise ValueError("payment review task was not found")
        appointment = connection.execute(
            "SELECT * FROM appointments WHERE id = ?",
            (str(task["appointment_id"]),),
        ).fetchone()
        details = json.loads(str(task["details_json"]))
        evidence = connection.execute(
            "SELECT * FROM payment_evidence WHERE id = ?",
            (str(details.get("evidence_id") or ""),),
        ).fetchone()
        if not appointment or not evidence:
            raise ValueError("payment review data is incomplete")
        return task, appointment, evidence

    @staticmethod
    def _load_handoff_task(
        connection: sqlite3.Connection,
        task_id: str,
    ) -> tuple[sqlite3.Row, sqlite3.Row]:
        task = connection.execute(
            """
            SELECT * FROM conversation_tasks
            WHERE id = ? AND task_type = 'take_over_conversation'
            """,
            (task_id,),
        ).fetchone()
        if not task:
            raise ValueError("人工接管任务不存在")
        conversation = connection.execute(
            "SELECT * FROM conversations WHERE id = ?",
            (str(task["conversation_id"]),),
        ).fetchone()
        if not conversation:
            raise ValueError("会话不存在")
        return task, conversation

    @staticmethod
    def _insert_system_outbox(
        connection: sqlite3.Connection,
        conversation_id: str,
        idempotency_key: str,
        message_type: str,
        payload: dict[str, object],
        available_at: datetime,
    ) -> None:
        now = _utc_now()
        connection.execute(
            """
            INSERT OR IGNORE INTO outbox_messages (
                id, conversation_id, actor, idempotency_key, message_type,
                payload, status, available_at, created_at, updated_at
            ) VALUES (?, ?, 'system', ?, ?, ?, 'pending', ?, ?, ?)
            """,
            (
                new_id(),
                conversation_id,
                idempotency_key,
                message_type,
                json.dumps(payload, ensure_ascii=False),
                _iso(available_at),
                _iso(now),
                _iso(now),
            ),
        )

    @staticmethod
    def _insert_audit_event(
        connection: sqlite3.Connection,
        conversation_id: str,
        appointment_id: str | None,
        actor_type: str,
        actor_id: str,
        event_type: str,
        details: dict[str, object],
        now: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO audit_events (
                id, conversation_id, appointment_id, actor_type, actor_id,
                event_type, details_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                new_id(),
                conversation_id,
                appointment_id,
                actor_type,
                actor_id,
                event_type,
                json.dumps(details, ensure_ascii=False),
                _iso(now),
            ),
        )

    def _require_executing_action(
        self,
        connection: sqlite3.Connection,
        job: ActionJob,
    ) -> None:
        row = connection.execute(
            "SELECT status FROM action_jobs WHERE id = ?",
            (job.id,),
        ).fetchone()
        if not row or str(row["status"]) != "executing":
            raise ValueError("action is not executing")

    @staticmethod
    def _finish_action(
        connection: sqlite3.Connection,
        action_id: str,
        status: str,
        error: str,
        now: datetime,
    ) -> None:
        connection.execute(
            """
            UPDATE action_jobs
            SET status = ?, locked_at = NULL, locked_by = '',
                last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (status, error, _iso(now), action_id),
        )

    def _finish_inbox_jobs(
        self,
        connection: sqlite3.Connection,
        job_ids: tuple[str, ...],
        status: str,
        error: str,
        now: datetime,
    ) -> None:
        placeholders = _placeholders(job_ids)
        connection.execute(
            f"""
            UPDATE inbox_jobs
            SET status = ?, locked_at = NULL, locked_by = '', last_error = ?, updated_at = ?
            WHERE id IN ({placeholders})
            """,
            (status, error, _iso(now), *job_ids),
        )

    def _request_handoff(
        self,
        connection: sqlite3.Connection,
        conversation_id: str,
        reason: str,
        now: datetime,
    ) -> None:
        cursor = connection.execute(
            """
            UPDATE conversations
            SET owner = 'human_pending', handoff_status = 'requested',
                handoff_reason = ?, updated_at = ?
            WHERE id = ? AND owner = 'ai'
            """,
            (reason, _iso(now), conversation_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("conversation is not available for human handoff")
        connection.execute(
            """
            INSERT INTO handoffs (
                id, conversation_id, reason, status, requested_at
            ) VALUES (?, ?, ?, 'requested', ?)
            """,
            (new_id(), conversation_id, reason, _iso(now)),
        )
        task_id = new_id()
        connection.execute(
            """
            INSERT INTO conversation_tasks (
                id, conversation_id, appointment_id, task_type, status,
                priority, details_json, created_at, updated_at
            ) VALUES (?, ?, NULL, 'take_over_conversation', 'open',
                      'urgent', ?, ?, ?)
            """,
            (
                task_id,
                conversation_id,
                json.dumps({"reason": reason}, ensure_ascii=False),
                _iso(now),
                _iso(now),
            ),
        )
        self._insert_system_outbox(
            connection,
            conversation_id,
            f"staff-handoff:{task_id}",
            "staff_textcard",
            {
                "task_id": task_id,
                "title": "客户请求人工接管",
                "description": f"接管原因：{reason[:120]}。请打开查看最近对话并处理。",
            },
            now,
        )

    def _insert_reply_outbox(
        self,
        connection: sqlite3.Connection,
        batch: InboxBatch,
        reply_text: str,
        now: datetime,
        prefix: str,
        *,
        actor: str = "ai",
    ) -> None:
        first_id = batch.messages[0].id
        last_id = batch.messages[-1].id
        connection.execute(
            """
            INSERT OR IGNORE INTO outbox_messages (
                id, conversation_id, actor, idempotency_key, message_type,
                payload, status, available_at, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'text', ?, 'pending', ?, ?, ?)
            """,
            (
                new_id(),
                batch.conversation_id,
                actor,
                f"{prefix}:{first_id}:{last_id}",
                json.dumps({"text": reply_text.strip()}, ensure_ascii=False),
                _iso(now),
                _iso(now),
                _iso(now),
            ),
        )

    @staticmethod
    def _insert_catalog_recommendation_outboxes(
        connection: sqlite3.Connection,
        conversation_id: str,
        source_message_id: str,
        catalog_refs: tuple[str, ...],
        now: datetime,
    ) -> int:
        unique_refs = tuple(dict.fromkeys(ref.strip().lower() for ref in catalog_refs if ref))[:3]
        queued = 0
        for position, catalog_ref in enumerate(unique_refs, start=1):
            image = connection.execute(
                """
                SELECT i.storage_path
                FROM catalog_products p
                JOIN catalog_product_images i ON i.model_code = p.model_code
                WHERE p.model_code = ? AND p.lifecycle_state = 'active'
                ORDER BY i.sort_order, i.id
                LIMIT 1
                """,
                (catalog_ref,),
            ).fetchone()
            if not image:
                continue
            available_at = now + timedelta(milliseconds=position * 250)
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO outbox_messages (
                    id, conversation_id, actor, idempotency_key, message_type,
                    payload, status, available_at, created_at, updated_at
                ) VALUES (?, ?, 'ai', ?, 'image', ?, 'pending', ?, ?, ?)
                """,
                (
                    new_id(),
                    conversation_id,
                    f"catalog-recommendation:{source_message_id}:{catalog_ref}",
                    json.dumps(
                        {"catalog_ref": catalog_ref, "position": 0},
                        ensure_ascii=False,
                    ),
                    _iso(available_at),
                    _iso(now),
                    _iso(now),
                ),
            )
            queued += 1
        return queued

    @staticmethod
    def _insert_catalog_resend_outbox(
        connection: sqlite3.Connection,
        conversation_id: str,
        source_message_id: str,
        now: datetime,
    ) -> int:
        rows = connection.execute(
            """
            SELECT payload
            FROM outbox_messages
            WHERE conversation_id = ? AND message_type = 'image'
              AND status IN ('pending', 'retry', 'sending', 'sent')
            ORDER BY created_at DESC
            LIMIT 20
            """,
            (conversation_id,),
        ).fetchall()
        payload: dict[str, object] | None = None
        for row in rows:
            candidate = json.loads(str(row["payload"]))
            if str(candidate.get("catalog_ref") or "").strip():
                payload = {
                    "catalog_ref": str(candidate["catalog_ref"]).strip().lower(),
                    "position": int(candidate.get("position") or 0),
                }
                break
        if payload is None:
            return 0
        available_at = now + timedelta(milliseconds=250)
        connection.execute(
            """
            INSERT OR IGNORE INTO outbox_messages (
                id, conversation_id, actor, idempotency_key, message_type,
                payload, status, available_at, created_at, updated_at
            ) VALUES (?, ?, 'ai', ?, 'image', ?, 'pending', ?, ?, ?)
            """,
            (
                new_id(),
                conversation_id,
                f"catalog-resend:{source_message_id}:{payload['catalog_ref']}",
                json.dumps(payload, ensure_ascii=False),
                _iso(available_at),
                _iso(now),
                _iso(now),
            ),
        )
        return 1

    def _load_memory(
        self,
        connection: sqlite3.Connection,
        conversation_id: str,
    ) -> ConversationMemory:
        row = connection.execute(
            "SELECT * FROM conversation_memory WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if not row:
            return ConversationMemory()
        return ConversationMemory(
            summary=str(row["summary"]),
            facts=_facts_from_json(str(row["facts_json"])),
            pending_tasks=tuple(json.loads(str(row["pending_tasks_json"]))),
            summary_through_message_id=str(row["summary_through_message_id"]),
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    @staticmethod
    def _upgrade_local_schema(connection: sqlite3.Connection) -> None:
        additions = {
            "conversations": {
                "handoff_reason": "TEXT NOT NULL DEFAULT ''",
                "assigned_staff_id": "TEXT NOT NULL DEFAULT ''",
                "handoff_summary": "TEXT NOT NULL DEFAULT ''",
            },
            "action_jobs": {
                "attempt_count": "INTEGER NOT NULL DEFAULT 0",
                "available_at": "TEXT NOT NULL DEFAULT ''",
                "locked_at": "TEXT",
                "locked_by": "TEXT NOT NULL DEFAULT ''",
                "last_error": "TEXT NOT NULL DEFAULT ''",
            },
        }
        for table, columns in additions.items():
            existing = {
                str(row["name"])
                for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
            }
            for name, definition in columns.items():
                if name not in existing:
                    connection.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")
        now = _iso(_utc_now())
        connection.execute(
            "UPDATE action_jobs SET available_at = ? WHERE available_at = ''",
            (now,),
        )
        connection.execute("DROP INDEX IF EXISTS action_jobs_ready")
        connection.execute(
            "CREATE INDEX action_jobs_ready "
            "ON action_jobs(status, available_at, created_at)"
        )

    def _transaction(self) -> _Transaction:
        return _Transaction(self._connect())


class _Transaction:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self.connection = connection

    def __enter__(self) -> sqlite3.Connection:
        self.connection.execute("BEGIN IMMEDIATE")
        return self.connection

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if exc_type is None:
                self.connection.commit()
            else:
                self.connection.rollback()
        finally:
            self.connection.close()


def _row_to_message(row: sqlite3.Row) -> StoredMessage:
    return StoredMessage(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        external_message_id=str(row["external_message_id"] or ""),
        actor=MessageActor(str(row["actor"])),
        message_type=str(row["message_type"]),
        text_content=str(row["text_content"]),
        raw_payload=json.loads(str(row["raw_payload"])),
        occurred_at=_parse_time(str(row["occurred_at"])),
        received_at=_parse_time(str(row["received_at"])),
    )


def _placeholders(values: Iterable[object]) -> str:
    count = sum(1 for _ in values)
    if count == 0:
        raise ValueError("at least one value is required")
    return ", ".join("?" for _ in range(count))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _customer_time_text(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.strip())
    except ValueError:
        return value

    hour = parsed.hour
    if hour < 6:
        period = "凌晨"
    elif hour < 9:
        period = "早上"
    elif hour < 12:
        period = "上午"
    elif hour < 14:
        period = "中午"
    elif hour < 18:
        period = "下午"
    else:
        period = "晚上"
    display_hour = hour if 1 <= hour <= 12 else abs(hour - 12)
    if display_hour == 0:
        display_hour = 12
    minute = f"{parsed.minute}分" if parsed.minute else ""
    return f"{parsed.month}月{parsed.day}日{period}{display_hour}点{minute}"


def _payment_review_message(reasons: list[str], amount: object) -> str:
    reason_set = set(reasons)
    if "duplicate_image" in reason_set:
        issue = "这张截图之前已经提交过，暂时不能直接用于当前预约。"
    elif "amount_mismatch" in reason_set and amount is not None:
        issue = "截图里的金额和试纱费200元没有匹配上。"
    elif "success_state_missing" in reason_set:
        issue = "截图里没有完整识别到支付成功状态。"
    elif "payee_mismatch" in reason_set:
        issue = "截图里的收款方信息没有匹配上。"
    else:
        issue = "这张截图里的付款信息没有完整识别到。"
    return (
        f"{issue}档期暂时还没有锁定。麻烦重新发一张完整的支付成功页面，"
        "需要清楚显示200元金额、收款方和支付成功状态。我也已经提醒店员复核。"
    )


CATALOG_IMAGE_SEND_INTENT = re.compile(
    r"(?:发|再发|重发).{0,20}(?:图|照片|图片|一款|两款|三款)|"
    r"(?:图片|照片).{0,12}(?:发|给你看)"
)


def _catalog_image_send_intent(text: str) -> bool:
    return bool(CATALOG_IMAGE_SEND_INTENT.search(text))


def _catalog_image_resend_requested(messages: tuple[StoredMessage, ...]) -> bool:
    text = " ".join(
        message.text_content
        for message in messages
        if message.actor is MessageActor.CUSTOMER
    )
    return any(
        phrase in text
        for phrase in (
            "没看到",
            "没收到",
            "没有看到",
            "没有收到",
            "再发",
            "重发",
            "重新发",
            "图片呢",
            "照片呢",
            "图呢",
        )
    )


def _facts_to_json(facts: tuple[MemoryFact, ...]) -> str:
    return json.dumps(
        [
            {
                "key": fact.key,
                "value": fact.value,
                "source": fact.source.value,
                "evidence_message_id": fact.evidence_message_id,
                "confidence": fact.confidence,
                "updated_at": _iso(fact.updated_at),
                "expires_at": _iso(fact.expires_at) if fact.expires_at else None,
            }
            for fact in facts
        ],
        ensure_ascii=False,
    )


def _facts_from_json(value: str) -> tuple[MemoryFact, ...]:
    items = json.loads(value)
    return tuple(
        MemoryFact(
            key=str(item["key"]),
            value=item.get("value"),
            source=FactSource(str(item["source"])),
            evidence_message_id=str(item.get("evidence_message_id") or ""),
            confidence=float(item.get("confidence", 1.0)),
            updated_at=_parse_time(str(item["updated_at"])),
            expires_at=(
                _parse_time(str(item["expires_at"])) if item.get("expires_at") else None
            ),
        )
        for item in items
    )
