"""
YouTube metadata + transcript extraction.
- YouTube oEmbed API → title, thumbnail, channel (no auth required)
- youtube-transcript-api → transcript text

Set YOUTUBE_COOKIES env var to the contents of a Netscape-format cookies.txt
file exported from a logged-in YouTube session. This bypasses YouTube's bot
detection on datacenter IPs. Without it, transcripts may fail on Vercel.
"""

import os
import re
import tempfile

import httpx
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound


def _cookies_path() -> str | None:
    """Write YOUTUBE_COOKIES env var to a temp file and return the path, or None."""
    content = os.environ.get("YOUTUBE_COOKIES", "").strip()
    if not content:
        return None
    # Vercel may store multiline values with escaped \n instead of real newlines
    content = content.replace("\\n", "\n")
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False)
    f.write(content)
    f.close()
    return f.name


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
    """Return list of {text, start, duration} segments.
    Tries English first, then any available transcript.
    Uses YOUTUBE_COOKIES if set to bypass datacenter IP blocking."""
    cookies = _cookies_path()

    try:
        return YouTubeTranscriptApi.get_transcript(
            youtube_id, languages=["en", "en-US", "en-GB"], cookies=cookies
        )
    except (NoTranscriptFound, TranscriptsDisabled):
        pass

    # Fall back to any available transcript (manual or auto-generated).
    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(youtube_id, cookies=cookies)
        transcript = next(iter(transcript_list))
        return transcript.fetch()
    except StopIteration:
        raise RuntimeError("No transcript available for this video.")
    except Exception as e:
        raise RuntimeError(f"Could not retrieve transcript: {e}")


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
