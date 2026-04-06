"""
Supabase (PostgreSQL) persistence layer.
Set DATABASE_URL to your Supabase connection string (use the pooler URL for serverless).
"""

import os
import json
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg2
import psycopg2.extras


@contextmanager
def get_conn():
    conn = psycopg2.connect(
        os.environ["DATABASE_URL"],
        cursor_factory=psycopg2.extras.RealDictCursor,
    )
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Videos ────────────────────────────────────────────────────────────────────

def add_video(youtube_id: str, url: str) -> int:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO videos (youtube_id, url, added_at)
               VALUES (%s, %s, %s)
               ON CONFLICT (youtube_id) DO NOTHING
               RETURNING id""",
            (youtube_id, url, _now()),
        )
        row = cur.fetchone()
        if row is None:
            cur.execute("SELECT id FROM videos WHERE youtube_id = %s", (youtube_id,))
            row = cur.fetchone()
        return row["id"]


def update_video_meta(video_id: int, title: str, thumbnail: str, duration: int, channel: str):
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE videos SET title=%s, thumbnail=%s, duration=%s, channel=%s WHERE id=%s",
            (title, thumbnail, duration, channel, video_id),
        )


def set_video_status(video_id: int, status: str, error_msg: str = None):
    with get_conn() as conn:
        cur = conn.cursor()
        if status == "done":
            cur.execute(
                "UPDATE videos SET status=%s, summarized_at=%s, error_msg=NULL WHERE id=%s",
                (status, _now(), video_id),
            )
        else:
            cur.execute(
                "UPDATE videos SET status=%s, error_msg=%s WHERE id=%s",
                (status, error_msg, video_id),
            )


def get_all_videos() -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM videos ORDER BY added_at DESC")
        return [dict(r) for r in cur.fetchall()]


def get_video(video_id: int) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM videos WHERE id=%s", (video_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def delete_video(video_id: int):
    with get_conn() as conn:
        conn.cursor().execute("DELETE FROM videos WHERE id=%s", (video_id,))


# ── Chapters ───────────────────────────────────────────────────────────────────

def save_chapters(video_id: int, chapters: list[dict]):
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("DELETE FROM chapters WHERE video_id=%s", (video_id,))
        cur.executemany(
            "INSERT INTO chapters (video_id, idx, title, start_sec, end_sec) VALUES (%s,%s,%s,%s,%s)",
            [(video_id, c["idx"], c["title"], c["start_sec"], c.get("end_sec")) for c in chapters],
        )


def update_chapter_summary(video_id: int, idx: int, summary: str):
    with get_conn() as conn:
        conn.cursor().execute(
            "UPDATE chapters SET summary=%s WHERE video_id=%s AND idx=%s",
            (summary, video_id, idx),
        )


def get_chapters(video_id: int) -> list[dict]:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM chapters WHERE video_id=%s ORDER BY idx", (video_id,))
        return [dict(r) for r in cur.fetchall()]


# ── Summary ────────────────────────────────────────────────────────────────────

def save_summary(video_id: int, overall: str, key_points: list[str]):
    with get_conn() as conn:
        conn.cursor().execute(
            """INSERT INTO video_summary (video_id, overall_summary, key_points, created_at)
               VALUES (%s, %s, %s, %s)
               ON CONFLICT (video_id) DO UPDATE SET
                 overall_summary = EXCLUDED.overall_summary,
                 key_points      = EXCLUDED.key_points,
                 created_at      = EXCLUDED.created_at""",
            (video_id, overall, json.dumps(key_points), _now()),
        )


def get_summary(video_id: int) -> dict | None:
    with get_conn() as conn:
        cur = conn.cursor()
        cur.execute("SELECT * FROM video_summary WHERE video_id=%s", (video_id,))
        row = cur.fetchone()
        if not row:
            return None
        d = dict(row)
        d["key_points"] = json.loads(d["key_points"] or "[]")
        return d
