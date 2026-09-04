PRAGMA foreign_keys = ON;

CREATE TABLE customers (
    id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    phone TEXT NOT NULL,
    name TEXT,
    email TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    UNIQUE (business_id, phone)
);

CREATE TABLE calls (
    id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    customer_id TEXT,
    call_sid TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    duration_seconds INTEGER,
    status TEXT NOT NULL DEFAULT 'active',
    summary TEXT,

    FOREIGN KEY (customer_id)
        REFERENCES customers(id)
        ON DELETE SET NULL
);

CREATE TABLE leads (
    id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    customer_id TEXT,
    call_id TEXT,
    intent TEXT,
    goal TEXT,
    current_situation TEXT,
    problem TEXT,
    previous_attempts TEXT,
    desired_outcome TEXT,
    experience TEXT,
    location TEXT,
    timeline TEXT,
    training_preference TEXT,
    availability TEXT,
    engagement INTEGER DEFAULT 0,
    program_fit INTEGER DEFAULT 0,
    goal_clarity INTEGER DEFAULT 0,
    next_step_intent TEXT,
    needs_human INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,

    FOREIGN KEY (customer_id)
        REFERENCES customers(id)
        ON DELETE SET NULL,

    FOREIGN KEY (call_id)
        REFERENCES calls(id)
        ON DELETE SET NULL
);

CREATE TABLE call_messages (
    id TEXT PRIMARY KEY,
    business_id TEXT NOT NULL,
    call_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL,

    FOREIGN KEY (call_id)
        REFERENCES calls(id)
        ON DELETE CASCADE
);

CREATE INDEX idx_customers_business_phone
    ON customers(business_id, phone);

CREATE INDEX idx_calls_customer
    ON calls(customer_id);

CREATE INDEX idx_calls_business
    ON calls(business_id);

CREATE INDEX idx_leads_customer
    ON leads(customer_id);

CREATE INDEX idx_leads_call
    ON leads(call_id);

CREATE INDEX idx_messages_call
    ON call_messages(call_id);