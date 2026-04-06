"""
YouTube metadata + transcript extraction.
- yt-dlp Python API → video metadata, chapters, auto-subs (no subprocess)
- youtube-transcript-api → transcript text (primary, faster)
"""

import re
import json
import glob
import os
import yt_dlp
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound


def extract_video_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/|shorts/)([A-Za-z0-9_-]{11})",
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            return m.group(1)
    return None


def fetch_metadata(youtube_id: str) -> dict:
    """Return title, thumbnail, duration, channel, and chapters via yt-dlp Python API."""
    url = f"https://www.youtube.com/watch?v={youtube_id}"
    ydl_opts = {"quiet": True, "no_warnings": True}

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=False)

    raw_chapters = info.get("chapters") or []
    chapters = []
    for i, ch in enumerate(raw_chapters):
        end = raw_chapters[i + 1]["start_time"] if i + 1 < len(raw_chapters) else info.get("duration")
        chapters.append({
            "idx": i,
            "title": ch["title"],
            "start_sec": ch["start_time"],
            "end_sec": end,
        })

    return {
        "title": info.get("title", "Untitled"),
        "thumbnail": info.get("thumbnail"),
        "duration": info.get("duration", 0),
        "channel": info.get("channel") or info.get("uploader"),
        "chapters": chapters,
    }


def fetch_transcript(youtube_id: str) -> list[dict]:
    """Return list of {text, start, duration} segments."""
    try:
        return YouTubeTranscriptApi.get_transcript(youtube_id)
    except (TranscriptsDisabled, NoTranscriptFound):
        pass

    # Fallback: pull auto-generated subs via yt-dlp Python API
    output_tmpl = f"/tmp/yt_sub_{youtube_id}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writeautomaticsub": True,
        "subtitleslangs": ["en"],
        "subtitlesformat": "json3",
        "outtmpl": output_tmpl,
    }

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([f"https://www.youtube.com/watch?v={youtube_id}"])

    sub_files = glob.glob(f"{output_tmpl}*.json3")
    if not sub_files:
        raise RuntimeError("No transcript available for this video.")

    with open(sub_files[0]) as f:
        data = json.load(f)

    segments = []
    for event in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in event.get("segs", [])).strip()
        if text:
            segments.append({
                "text": text,
                "start": event.get("tStartMs", 0) / 1000,
                "duration": event.get("dDurationMs", 0) / 1000,
            })

    for fp in sub_files:
        os.remove(fp)

    return segments


def slice_transcript_for_chapter(transcript: list[dict], start_sec: float, end_sec: float | None) -> str:
    """Extract transcript text that falls within [start_sec, end_sec)."""
    parts = []
    for seg in transcript:
        seg_start = seg["start"]
        seg_end = seg_start + seg.get("duration", 0)
        if end_sec and seg_start >= end_sec:
            break
        if seg_end > start_sec:
            parts.append(seg["text"])
    return " ".join(parts).strip()


def build_chapter_transcripts(transcript: list[dict], chapters: list[dict]) -> list[dict]:
    """Attach transcript text to each chapter dict."""
    return [
        {**ch, "transcript": slice_transcript_for_chapter(transcript, ch["start_sec"], ch.get("end_sec"))}
        for ch in chapters
    ]
