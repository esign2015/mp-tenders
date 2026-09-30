CREATE TABLE IF NOT EXISTS users (
  telegram_id INTEGER PRIMARY KEY,
  first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
  username TEXT NOT NULL DEFAULT '', name TEXT NOT NULL DEFAULT '',
  mobile TEXT NOT NULL DEFAULT '', mobile_verified INTEGER NOT NULL DEFAULT 0,
  signup_at TEXT NOT NULL, last_login_at TEXT NOT NULL,
  login_count INTEGER NOT NULL DEFAULT 0,
  email TEXT NOT NULL DEFAULT '', district TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS login_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, telegram_id INTEGER NOT NULL, login_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_login_events_time ON login_events(login_at);
CREATE INDEX IF NOT EXISTS idx_login_events_user ON login_events(telegram_id);
CREATE INDEX IF NOT EXISTS idx_users_signup ON users(signup_at);
