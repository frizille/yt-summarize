"""
Summarization via Claude API.
- Per-chapter summaries (from sliced transcript)
- Overall video summary + key points
"""

import json
import os
import anthropic

MODEL = "claude-sonnet-4-20250514"
client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _call(system: str, user: str, max_tokens: int = 512) -> str:
    resp = client.messages.create(
        model=MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return resp.content[0].text.strip()


def summarize_chapter(chapter_title: str, transcript_text: str, video_title: str) -> str:
    if not transcript_text:
        return "_No transcript available for this chapter._"

    system = (
        "You are a precise, intelligent summarizer. "
        "Summarize only what is said in the provided transcript segment. "
        "Be concise: 3–6 sentences. Use plain prose, no bullet points."
    )
    user = (
        f"Video: {video_title}\n"
        f"Chapter: {chapter_title}\n\n"
        f"Transcript:\n{transcript_text[:6000]}"
    )
    return _call(system, user)


def summarize_overall(video_title: str, chapter_summaries: list[dict]) -> tuple[str, list[str]]:
    chapters_text = "\n\n".join(
        f"### {c['title']}\n{c['summary']}" for c in chapter_summaries if c.get("summary")
    )
    system = (
        "You are an expert at synthesizing video content into clear executive summaries. "
        "Return ONLY valid JSON with two fields:\n"
        '  "summary": a 4–8 sentence overall summary of the entire video\n'
        '  "key_points": a list of 4–7 short, punchy key takeaways (each ≤ 20 words)\n'
        "No markdown, no preamble, just the JSON object."
    )
    user = f"Video: {video_title}\n\nChapter summaries:\n{chapters_text}"
    raw = _call(system, user, max_tokens=1024)
    try:
        data = json.loads(raw)
        return data.get("summary", ""), data.get("key_points", [])
    except json.JSONDecodeError:
        return raw, []


def summarize_no_chapters(video_title: str, full_transcript: str) -> tuple[str, list[str]]:
    """For videos without chapters, summarize the full transcript directly."""
    system = (
        "You are an expert at summarizing YouTube video content. "
        "Return ONLY valid JSON with two fields:\n"
        '  "summary": a 5–10 sentence overall summary\n'
        '  "key_points": a list of 4–7 short, punchy key takeaways (each ≤ 20 words)\n'
        "No markdown, no preamble, just the JSON object."
    )
    user = f"Video: {video_title}\n\nTranscript:\n{full_transcript[:12000]}"
    raw = _call(system, user, max_tokens=1024)
    try:
        data = json.loads(raw)
        return data.get("summary", ""), data.get("key_points", [])
    except json.JSONDecodeError:
        return raw, []
