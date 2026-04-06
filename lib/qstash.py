"""
QStash (Upstash) helpers.
- publish_job: enqueue a processing job for a video
- verify_request: validate the QSTASH signature on incoming webhooks

Required env vars (auto-populated by Vercel Upstash integration):
  QSTASH_TOKEN, QSTASH_CURRENT_SIGNING_KEY, QSTASH_NEXT_SIGNING_KEY

APP_URL should be set to your stable production URL (e.g. https://your-app.vercel.app).
If absent, VERCEL_URL is used (changes per deployment).
"""

import os
from upstash_qstash import QStash
from upstash_qstash import Receiver


def _process_url() -> str:
    if url := os.environ.get("APP_URL"):
        return f"{url.rstrip('/')}/api/process"
    if vercel_url := os.environ.get("VERCEL_URL"):
        return f"https://{vercel_url}/api/process"
    raise RuntimeError("Set APP_URL or VERCEL_URL so QStash knows where to deliver jobs.")


def publish_job(video_id: int) -> None:
    """Publish a processing job to QStash. Returns immediately."""
    client = QStash(token=os.environ["QSTASH_TOKEN"])
    client.message.publish_json(
        url=_process_url(),
        body={"video_id": video_id},
    )


def verify_request(body: str | bytes, signature: str, request_url: str) -> None:
    """
    Verify the upstash-signature header from an incoming QStash webhook.
    Raises an exception if the signature is missing or invalid.
    """
    if isinstance(body, bytes):
        body = body.decode()
    receiver = Receiver(
        current_signing_key=os.environ["QSTASH_CURRENT_SIGNING_KEY"],
        next_signing_key=os.environ["QSTASH_NEXT_SIGNING_KEY"],
    )
    receiver.verify(
        signature=signature,
        body=body,
        url=request_url,
    )
