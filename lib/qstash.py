"""
QStash (Upstash) helpers — no SDK dependency.
- publish_job:    POST to QStash REST API via httpx
- verify_request: verify upstash-signature JWT using stdlib HMAC-SHA256

Required env vars (auto-populated by Vercel Upstash integration):
  QSTASH_TOKEN, QSTASH_CURRENT_SIGNING_KEY, QSTASH_NEXT_SIGNING_KEY

APP_URL should be set to your stable production URL (e.g. https://your-app.vercel.app).
If absent, VERCEL_URL (auto-set by Vercel) is used — but this changes per deployment.
"""

import base64
import hashlib
import hmac
import json
import os
import time
from urllib.parse import quote

import httpx

QSTASH_API = "https://qstash.upstash.io/v2/publish"


def _process_url() -> str:
    if url := os.environ.get("APP_URL"):
        return f"{url.rstrip('/')}/api/process"
    if vercel_url := os.environ.get("VERCEL_URL"):
        return f"https://{vercel_url}/api/process"
    raise RuntimeError("Set APP_URL or VERCEL_URL so QStash knows where to deliver jobs.")


def publish_job(video_id: int) -> None:
    """Enqueue a processing job. Returns immediately after QStash accepts it."""
    process_url = _process_url()
    resp = httpx.post(
        f"{QSTASH_API}/{quote(process_url, safe='')}",
        headers={
            "Authorization": f"Bearer {os.environ['QSTASH_TOKEN']}",
            "Content-Type": "application/json",
        },
        json={"video_id": video_id},
        timeout=10,
    )
    resp.raise_for_status()


def verify_request(body: str | bytes, signature: str, request_url: str) -> None:
    """
    Verify the upstash-signature JWT from an incoming QStash webhook.
    Tries QSTASH_CURRENT_SIGNING_KEY first, then QSTASH_NEXT_SIGNING_KEY.
    Raises ValueError if neither key validates the signature.
    """
    if isinstance(body, bytes):
        body = body.decode()

    for key_name in ("QSTASH_CURRENT_SIGNING_KEY", "QSTASH_NEXT_SIGNING_KEY"):
        key = os.environ.get(key_name, "")
        if key and _verify_jwt(signature, key, body, request_url):
            return

    raise ValueError("QStash signature verification failed.")


# ── JWT verification (stdlib only) ────────────────────────────────────────────

def _b64url_decode(s: str) -> bytes:
    s += "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s)


def _verify_jwt(token: str, key: str, body: str, url: str) -> bool:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return False

        header_payload = f"{parts[0]}.{parts[1]}"
        expected_sig = base64.urlsafe_b64encode(
            hmac.new(key.encode(), header_payload.encode(), hashlib.sha256).digest()
        ).rstrip(b"=")

        if not hmac.compare_digest(expected_sig, parts[2].encode()):
            return False

        payload = json.loads(_b64url_decode(parts[1]))
        now = time.time()

        if payload.get("exp", 0) < now:
            return False
        if payload.get("nbf", now) > now + 1:  # 1 s clock-skew tolerance
            return False

        body_hash = base64.urlsafe_b64encode(
            hashlib.sha256(body.encode()).digest()
        ).decode().rstrip("=")
        if payload.get("body") != body_hash:
            return False

        if payload.get("sub") != url:
            return False

        return True
    except Exception:
        return False
