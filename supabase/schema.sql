-- Run this once in the Supabase SQL editor to create the schema.
-- If upgrading from a previous version, run the ALTER TABLE below to convert key_points to JSONB.

CREATE TABLE IF NOT EXISTS videos (
    id            SERIAL PRIMARY KEY,
    youtube_id    TEXT UNIQUE NOT NULL,
    title         TEXT,
    url           TEXT NOT NULL,
    thumbnail     TEXT,
    duration      INTEGER,
    channel       TEXT,
    status        TEXT NOT NULL DEFAULT 'queued',
    error_msg     TEXT,
    added_at      TEXT NOT NULL,
    summarized_at TEXT
);

CREATE TABLE IF NOT EXISTS chapters (
    id          SERIAL PRIMARY KEY,
    video_id    INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    idx         INTEGER NOT NULL,
    title       TEXT NOT NULL,
    start_sec   REAL NOT NULL,
    end_sec     REAL,
    summary     TEXT
);

CREATE TABLE IF NOT EXISTS video_summary (
    id              SERIAL PRIMARY KEY,
    video_id        INTEGER UNIQUE NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    overall_summary TEXT,
    key_points      JSONB,
    created_at      TEXT NOT NULL
);

-- If upgrading an existing database where key_points was TEXT, run:
-- ALTER TABLE video_summary ALTER COLUMN key_points TYPE JSONB USING key_points::jsonb;
