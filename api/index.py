"""
FastAPI app — single Vercel serverless entry point.
All /api/* requests are rewritten here via vercel.json.

Endpoints:
  POST   /api/videos              → add URL to queue + kick off processing
  GET    /api/videos              → list all videos
  GET    /api/videos/{id}         → video + chapters + summary
  GET    /api/videos/{id}/status  → polling endpoint
  POST   /api/videos/{id}/retry   → retry after error
  DELETE /api/videos/{id}         → remove from queue
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root is on sys.path so `lib` imports work on Vercel
sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import lib.database as db
import lib.youtube as yt
import lib.summarizer as sm

app = FastAPI(title="YT Summarizer")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request models ─────────────────────────────────────────────────────────────

class AddVideoRequest(BaseModel):
    url: str


# ── Background worker ──────────────────────────────────────────────────────────

async def process_video(video_id: int, youtube_id: str):
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
        raise


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.post("/api/videos", status_code=201)
async def add_video(body: AddVideoRequest, background_tasks: BackgroundTasks):
    youtube_id = yt.extract_video_id(body.url)
    if not youtube_id:
        raise HTTPException(400, "Could not parse a YouTube video ID from that URL.")

    video_id = db.add_video(youtube_id, body.url)
    video = db.get_video(video_id)

    if video["status"] == "queued":
        background_tasks.add_task(process_video, video_id, youtube_id)

    return {"id": video_id, "youtube_id": youtube_id, "status": video["status"]}


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
async def retry_video(video_id: int, background_tasks: BackgroundTasks):
    video = db.get_video(video_id)
    if not video:
        raise HTTPException(404, "Video not found.")
    if video["status"] not in ("error", "queued"):
        raise HTTPException(400, f"Cannot retry a video with status '{video['status']}'.")
    db.set_video_status(video_id, "queued")
    background_tasks.add_task(process_video, video_id, video["youtube_id"])
    return {"status": "queued"}


@app.delete("/api/videos/{video_id}", status_code=204)
def remove_video(video_id: int):
    if not db.get_video(video_id):
        raise HTTPException(404, "Video not found.")
    db.delete_video(video_id)
