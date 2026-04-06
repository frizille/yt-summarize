"""
YouTube metadata + transcript extraction.
- YouTube oEmbed API → title, thumbnail, channel (no auth required)
- Direct timedtext fetch with browser headers → transcript (bypasses bot detection)
- youtube-transcript-api → transcript fast path (may be blocked on cloud IPs)

Set YOUTUBE_COOKIES env var to Netscape-format cookies.txt content to
authenticate requests if bot detection persists.
"""

import http.cookiejar
import io
import json
import os
import re
import tempfile

import httpx
from youtube_transcript_api import YouTubeTranscriptApi, TranscriptsDisabled, NoTranscriptFound

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


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


def _make_client() -> httpx.Client:
    """Return an httpx client with browser headers and optional YouTube cookies."""
    cookies_path = _cookies_path()
    if cookies_path:
        jar = http.cookiejar.MozillaCookieJar(cookies_path)
        try:
            jar.load(ignore_discard=True, ignore_expires=True)
        except Exception:
            jar = None
    else:
        jar = None
    return httpx.Client(headers=_HEADERS, cookies=jar, follow_redirects=True, timeout=15)


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
    """Return title, thumbnail, channel via YouTube oEmbed."""
    oembed_url = (
        f"https://www.youtube.com/oembed"
        f"?url=https%3A%2F%2Fwww.youtube.com%2Fwatch%3Fv%3D{youtube_id}"
        f"&format=json"
    )
    with _make_client() as client:
        resp = client.get(oembed_url)
    resp.raise_for_status()
    data = resp.json()

    return {
        "title": data.get("title", "Untitled"),
        "thumbnail": data.get("thumbnail_url"),
        "duration": 0,
        "channel": data.get("author_name"),
        "chapters": [],
    }


def _fetch_transcript_direct(youtube_id: str) -> list[dict]:
    """
    Fetch transcript by scraping the YouTube watch page directly.
    Extracts caption track URLs from ytInitialPlayerResponse and
    downloads the JSON3 transcript, bypassing youtube-transcript-api.
    """
    watch_url = f"https://www.youtube.com/watch?v={youtube_id}"
    with _make_client() as client:
        resp = client.get(watch_url)
    resp.raise_for_status()

    match = re.search(r"ytInitialPlayerResponse\s*=\s*(\{.+?\})\s*;", resp.text)
    if not match:
        raise RuntimeError("Could not parse YouTube page response.")

    player = json.loads(match.group(1))

    try:
        tracks = player["captions"]["playerCaptionsTracklistRenderer"]["captionTracks"]
    except (KeyError, TypeError):
        raise RuntimeError("No captions found for this video.")

    if not tracks:
        raise RuntimeError("No caption tracks available for this video.")

    # Prefer English; fall back to first available
    track = next(
        (t for t in tracks if t.get("languageCode", "").startswith("en")),
        tracks[0],
    )
    track_url = track["baseUrl"] + "&fmt=json3"

    with _make_client() as client:
        resp = client.get(track_url)
    resp.raise_for_status()

    data = resp.json()
    segments = []
    for event in data.get("events", []):
        text = "".join(s.get("utf8", "") for s in event.get("segs", [])).strip()
        if text and text != "\n":
            segments.append({
                "text": text,
                "start": event.get("tStartMs", 0) / 1000,
                "duration": event.get("dDurationMs", 0) / 1000,
            })
    return segments


def fetch_transcript(youtube_id: str) -> list[dict]:
    """Return list of {text, start, duration} segments."""
    # Try direct fetch first — more reliable on cloud IPs
    try:
        return _fetch_transcript_direct(youtube_id)
    except Exception:
        pass

    # Fall back to youtube-transcript-api
    cookies = _cookies_path()
    try:
        return YouTubeTranscriptApi.get_transcript(
            youtube_id, languages=["en", "en-US", "en-GB"], cookies=cookies
        )
    except (NoTranscriptFound, TranscriptsDisabled):
        pass

    try:
        transcript_list = YouTubeTranscriptApi.list_transcripts(youtube_id, cookies=cookies)
        transcript = next(iter(transcript_list))
        return transcript.fetch()
    except StopIteration:
        pass
    except Exception:
        pass

    raise RuntimeError("No transcript could be retrieved for this video.")


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
