CREATE TABLE IF NOT EXISTS manual_clip_jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_file TEXT NOT NULL,
    user_start_sec INTEGER NOT NULL,
    detected_start_sec INTEGER,
    final_start_sec INTEGER,
    duration_sec INTEGER NOT NULL DEFAULT 1200,
    tournament_id TEXT NOT NULL,
    output_file TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_manual_clip_jobs_status
    ON manual_clip_jobs(status);

CREATE INDEX IF NOT EXISTS idx_manual_clip_jobs_tournament
    ON manual_clip_jobs(tournament_id);
