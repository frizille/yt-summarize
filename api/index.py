"""
FastAPI app — single Vercel serverless entry point.
All /api/* requests are rewritten here via vercel.json.

Processing is decoupled via QStash:
  POST /api/videos     → insert DB record, publish QStash job → 201 immediately
  POST /api/process    → QStash webhook: runs full pipeline, always returns 200

Other endpoints:
  GET  /api/videos              → list all videos
  GET  /api/videos/{id}         → video + chapters + summary
  GET  /api/videos/{id}/status  → polling endpoint
  POST /api/videos/{id}/retry   → reset status, re-publish QStash job
  DELETE /api/videos/{id}       → remove from queue
"""

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel

import lib.database as db
import lib.youtube as yt
import lib.summarizer as sm
import lib.qstash as qsh

app = FastAPI(title="YT Summarizer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── API secret middleware ──────────────────────────────────────────────────────
# /api/process is exempted — it's authenticated via QStash signature instead.

@app.middleware("http")
async def require_api_secret(request: Request, call_next):
    if request.url.path != "/api/process":
        expected = os.environ.get("API_SECRET")
        if expected and request.headers.get("x-api-secret") != expected:
            return JSONResponse(status_code=401, content={"detail": "Unauthorized."})
    return await call_next(request)


# ── Request models ─────────────────────────────────────────────────────────────

class AddVideoRequest(BaseModel):
    url: str


# ── Processing pipeline ────────────────────────────────────────────────────────

async def _run_processing(video_id: int) -> None:
    """
    Full extraction + summarization pipeline.
    Writes status=error to DB on failure instead of raising,
    so the QStash webhook can always return 200.
    """
    video = db.get_video(video_id)
    if not video:
        return

    youtube_id = video["youtube_id"]

    try:
        db.set_video_status(video_id, "processing")

        meta = await asyncio.to_thread(yt.fetch_metadata, youtube_id)
        db.update_video_meta(
            video_id,
            title=meta["title"],
            thumbnail=meta["thumbnail"],
            duration=meta["duration"],
            channel=meta["channel"],
        )

        chapters = meta["chapters"]
        if chapters:
            db.save_chapters(video_id, chapters)

        transcript = await asyncio.to_thread(yt.fetch_transcript, youtube_id)

        if chapters:
            chapter_transcripts = yt.build_chapter_transcripts(transcript, chapters)
            chapter_summaries = []
            for ch in chapter_transcripts:
                summary = await asyncio.to_thread(
                    sm.summarize_chapter, ch["title"], ch["transcript"], meta["title"]
                )
                db.update_chapter_summary(video_id, ch["idx"], summary)
                chapter_summaries.append({"title": ch["title"], "summary": summary})

            overall, key_points = await asyncio.to_thread(
                sm.summarize_overall, meta["title"], chapter_summaries
            )
        else:
            full_text = " ".join(s["text"] for s in transcript)
            overall, key_points = await asyncio.to_thread(
                sm.summarize_no_chapters, meta["title"], full_text
            )

        db.save_summary(video_id, overall, key_points)
        db.set_video_status(video_id, "done")

    except Exception as exc:
        db.set_video_status(video_id, "error", str(exc))


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/api/videos", status_code=201)
async def add_video(body: AddVideoRequest):
    youtube_id = yt.extract_video_id(body.url)
    if not youtube_id:
        raise HTTPException(400, "Could not parse a YouTube video ID from that URL.")

    video_id = db.add_video(youtube_id, body.url)
    video = db.get_video(video_id)

    if video["status"] == "queued":
        qsh.publish_job(video_id)

    return {"id": video_id, "youtube_id": youtube_id, "status": video["status"]}


@app.post("/api/process")
async def process_webhook(request: Request):
    """
    QStash webhook. Verifies the upstash-signature header, runs the pipeline,
    and ALWAYS returns HTTP 200. Errors are written to the DB to prevent
    QStash from retrying jobs that failed for a deterministic reason.
    """
    body_bytes = await request.body()
    signature = request.headers.get("upstash-signature", "")
    request_url = str(request.url).split("?")[0]

    try:
        qsh.verify_request(body_bytes, signature, request_url)
    except Exception:
        raise HTTPException(401, "Invalid QStash signature.")

    payload = json.loads(body_bytes)
    video_id = payload.get("video_id")

    if video_id:
        await _run_processing(video_id)

    return {"ok": True}


@app.get("/api/videos")
def list_videos():
    return db.get_all_videos()


@app.get("/api/videos/{video_id}")
def get_video(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found.")
    chapters = db.get_chapters(video_id)
    summary = db.get_summary(video_id)
    return {**video, "chapters": chapters, "summary": summary}


@app.get("/api/videos/{video_id}/status")
def get_status(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found.")
    return {"id": video_id, "status": video["status"], "error_msg": video.get("error_msg")}


@app.post("/api/videos/{video_id}/retry")
async def retry_video(video_id: int):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found.")
    if video["status"] not in ("error", "queued"):
        raise HTTPException(400, f"Cannot retry a video with status '{video['status']}'.")
    db.set_video_status(video_id, "queued")
    qsh.publish_job(video_id)
    return {"status": "queued"}


@app.delete("/api/videos/{video_id}", status_code=204)
def remove_video(video_id: int):
    if not db.get_video(video_id):
        raise HTTPException(404, "Video not found.")
    db.delete_video(video_id)
