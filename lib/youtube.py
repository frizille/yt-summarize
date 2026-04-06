"""
YouTube metadata + transcript extraction.
- YouTube oEmbed API → title, thumbnail, channel (no auth required)
- youtube-transcript-api → transcript text
"""

import re

import httpx
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
    """Return title, thumbnail, channel via YouTube oEmbed. No yt-dlp (blocked on datacenter IPs)."""
    oembed_url = (
        f"https://www.youtube.com/oembed"
        f"?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D{youtube_id}"
        f"&format=json"
    )
    resp = httpx.get(oembed_url, timeout=10, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()

    return {
        "title": data.get("title", "Untitled"),
        "thumbnail": data.get("thumbnail_url"),
        "duration": 0,
        "channel": data.get("author_name"),
        "chapters": [],  # oEmbed doesn't expose chapters; use full-transcript summarization
    }


def fetch_transcript(youtube_id: str) -> list[dict]:
    """Return list of {text, start, duration} segments."""
    try:
        return YouTubeTranscriptApi.get_transcript(youtube_id)
    except (TranscriptsDisabled, NoTranscriptFound):
        raise RuntimeError("No transcript available for this video.")


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
