BEGIN;

CREATE TABLE conversations (
    id UUID PRIMARY KEY,
    channel TEXT NOT NULL,
    channel_account_id TEXT NOT NULL,
    external_user_id TEXT NOT NULL,
    owner TEXT NOT NULL CHECK (owner IN ('ai', 'human_pending', 'human')),
    handoff_status TEXT NOT NULL CHECK (
        handoff_status IN ('none', 'requested', 'active', 'failed', 'return_pending')
    ),
    handoff_reason TEXT NOT NULL DEFAULT '',
    assigned_staff_id TEXT NOT NULL DEFAULT '',
    handoff_summary TEXT NOT NULL DEFAULT '',
    rolling_summary TEXT NOT NULL DEFAULT '',
    summary_through_message_id UUID,
    last_message_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (channel, channel_account_id, external_user_id)
);

CREATE TABLE messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    external_message_id TEXT,
    actor TEXT NOT NULL CHECK (actor IN ('customer', 'ai', 'staff', 'system')),
    message_type TEXT NOT NULL,
    text_content TEXT NOT NULL DEFAULT '',
    media_storage_key TEXT NOT NULL DEFAULT '',
    raw_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    occurred_at TIMESTAMPTZ NOT NULL,
    received_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX messages_external_id_unique
    ON messages (conversation_id, external_message_id)
    WHERE external_message_id IS NOT NULL;

CREATE INDEX messages_conversation_timeline
    ON messages (conversation_id, occurred_at, id);

CREATE TABLE inbox_jobs (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    message_id UUID NOT NULL UNIQUE REFERENCES messages(id),
    status TEXT NOT NULL CHECK (status IN ('pending', 'processing', 'done', 'retry', 'dead')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL,
    locked_at TIMESTAMPTZ,
    locked_by TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX inbox_jobs_ready
    ON inbox_jobs (available_at, created_at)
    WHERE status IN ('pending', 'retry');

CREATE TABLE outbox_messages (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    actor TEXT NOT NULL CHECK (actor IN ('ai', 'staff', 'system')),
    idempotency_key TEXT NOT NULL UNIQUE,
    message_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('pending', 'sending', 'sent', 'cancelled', 'retry', 'dead')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL,
    locked_at TIMESTAMPTZ,
    locked_by TEXT NOT NULL DEFAULT '',
    external_message_id TEXT,
    sent_at TIMESTAMPTZ,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX outbox_messages_ready
    ON outbox_messages (available_at, created_at)
    WHERE status IN ('pending', 'retry');

CREATE TABLE customer_profiles (
    conversation_id UUID PRIMARY KEY REFERENCES conversations(id),
    customer_name TEXT NOT NULL DEFAULT '',
    phone_encrypted TEXT NOT NULL DEFAULT '',
    wedding_date TEXT NOT NULL DEFAULT '',
    venue TEXT NOT NULL DEFAULT '',
    style_preferences JSONB NOT NULL DEFAULT '[]'::jsonb,
    optional_facts JSONB NOT NULL DEFAULT '{}'::jsonb,
    expires_at TIMESTAMPTZ,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE appointments (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    status TEXT NOT NULL,
    customer_name TEXT NOT NULL DEFAULT '',
    phone_encrypted TEXT NOT NULL DEFAULT '',
    requested_time_text TEXT NOT NULL DEFAULT '',
    confirmed_start_at TIMESTAMPTZ,
    confirmed_end_at TIMESTAMPTZ,
    proposed_reschedule_text TEXT NOT NULL DEFAULT '',
    free_reschedules_used INTEGER NOT NULL DEFAULT 0,
    payment_evidence_id UUID,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX appointments_conversation
    ON appointments (conversation_id, created_at DESC);

CREATE INDEX appointments_confirmed_time
    ON appointments (confirmed_start_at, confirmed_end_at)
    WHERE status = 'confirmed';

CREATE TABLE payment_evidence (
    id UUID PRIMARY KEY,
    appointment_id UUID NOT NULL REFERENCES appointments(id),
    source_message_id UUID NOT NULL UNIQUE REFERENCES messages(id),
    media_storage_key TEXT NOT NULL,
    extracted_amount NUMERIC(12, 2),
    extracted_payee TEXT NOT NULL DEFAULT '',
    transaction_reference TEXT NOT NULL DEFAULT '',
    shows_success BOOLEAN NOT NULL DEFAULT FALSE,
    confidence NUMERIC(5, 4) NOT NULL DEFAULT 0,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'review')),
    decision_reasons JSONB NOT NULL DEFAULT '[]'::jsonb,
    extractor_version TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL
);

ALTER TABLE appointments
    ADD CONSTRAINT appointments_payment_evidence_fk
    FOREIGN KEY (payment_evidence_id) REFERENCES payment_evidence(id);

CREATE TABLE conversation_tasks (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    appointment_id UUID REFERENCES appointments(id),
    task_type TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('open', 'claimed', 'resolved', 'cancelled')),
    priority TEXT NOT NULL CHECK (priority IN ('normal', 'urgent')),
    assigned_staff_id TEXT NOT NULL DEFAULT '',
    due_at TIMESTAMPTZ,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX conversation_tasks_open
    ON conversation_tasks (priority, due_at, created_at)
    WHERE status IN ('open', 'claimed');

CREATE TABLE action_jobs (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    source_message_id UUID NOT NULL REFERENCES messages(id),
    action_type TEXT NOT NULL,
    arguments JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL CHECK (
        status IN ('proposed', 'validated', 'executing', 'done', 'rejected', 'retry', 'dead')
    ),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    available_at TIMESTAMPTZ NOT NULL,
    last_error TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    UNIQUE (source_message_id, action_type)
);

CREATE INDEX action_jobs_ready
    ON action_jobs (available_at, created_at)
    WHERE status IN ('proposed', 'validated', 'retry');

CREATE TABLE handoffs (
    id UUID PRIMARY KEY,
    conversation_id UUID NOT NULL REFERENCES conversations(id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL,
    staff_id TEXT NOT NULL DEFAULT '',
    requested_at TIMESTAMPTZ NOT NULL,
    activated_at TIMESTAMPTZ,
    returned_at TIMESTAMPTZ,
    summary TEXT NOT NULL DEFAULT '',
    external_state JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE audit_events (
    id UUID PRIMARY KEY,
    conversation_id UUID REFERENCES conversations(id),
    appointment_id UUID REFERENCES appointments(id),
    actor_type TEXT NOT NULL,
    actor_id TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL,
    details JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL
);

CREATE INDEX audit_events_conversation
    ON audit_events (conversation_id, created_at DESC);

COMMIT;
