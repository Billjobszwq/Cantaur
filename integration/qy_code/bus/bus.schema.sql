PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bus_messages (
  message_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL,
  trace_id TEXT NOT NULL,
  parent_task_id TEXT,
  from_agent TEXT NOT NULL,
  to_agent TEXT NOT NULL,
  message_type TEXT NOT NULL,
  body_json TEXT NOT NULL,
  queue_status TEXT NOT NULL,
  retry_count INTEGER NOT NULL DEFAULT 0,
  available_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  acked_at TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS bus_audit (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_type TEXT NOT NULL,
  message_id TEXT,
  task_id TEXT,
  actor TEXT NOT NULL,
  note TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blackboard_entries (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL,
  entry_key TEXT NOT NULL,
  entry_value_json TEXT NOT NULL,
  updated_by TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_blackboard_task_key
  ON blackboard_entries(task_id, entry_key);

CREATE INDEX IF NOT EXISTS idx_bus_messages_to_status
  ON bus_messages(to_agent, queue_status, available_at);

CREATE INDEX IF NOT EXISTS idx_bus_messages_task_id
  ON bus_messages(task_id);

CREATE INDEX IF NOT EXISTS idx_bus_audit_task_id
  ON bus_audit(task_id);

