"""SuperChat API client.

Verified facts baked in:
- auth header: X-API-KEY
- pagination: limit + cursor 'after', response pagination.next_cursor
- messages are ONLY available via conversation export jobs
- export 'end' must not be in the future -> use now (UTC)
- finished status is "done" (NOT "completed" as the docs claim)
- download link is a temporary CDN url (~12h)
"""
import logging
import time
from datetime import datetime, timedelta, timezone

import requests

from . import config

log = logging.getLogger("superchat")


class SuperChatClient:
    def __init__(self, api_key=None, base_url=None, max_retries=5):
        self.base_url = (base_url or config.SUPERCHAT_BASE_URL).rstrip("/")
        self.max_retries = max_retries
        self.session = requests.Session()
        self.session.headers.update(
            {"X-API-KEY": api_key or config.SUPERCHAT_API_KEY}
        )

    # --- low level with backoff -------------------------------------------
    def _request(self, method, path, **kwargs):
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        delay = 1.0
        last_exc = None
        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self.session.request(method, url, timeout=60, **kwargs)
                if resp.status_code == 429 or resp.status_code >= 500:
                    log.warning(
                        "HTTP %s on %s (attempt %s/%s), backing off %ss",
                        resp.status_code, path, attempt, self.max_retries, delay,
                    )
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    continue
                return resp
            except requests.RequestException as exc:
                last_exc = exc
                log.warning(
                    "Request error on %s (attempt %s/%s): %s",
                    path, attempt, self.max_retries, exc,
                )
                time.sleep(delay)
                delay = min(delay * 2, 30)
        if last_exc:
            raise last_exc
        return resp  # last response (likely error); caller handles

    # --- helpers -----------------------------------------------------------
    def get_me(self):
        resp = self._request("GET", "/me")
        resp.raise_for_status()
        return resp.json()

    def paginate(self, path, limit=100):
        """Yield each result item across all pages."""
        cursor = None
        while True:
            params = {"limit": limit}
            if cursor:
                params["after"] = cursor
            resp = self._request("GET", path, params=params)
            resp.raise_for_status()
            data = resp.json()
            for item in data.get("results", []):
                yield item
            cursor = (data.get("pagination") or {}).get("next_cursor")
            if not cursor:
                break

    def list_contacts(self):
        return list(self.paginate("/contacts"))

    def list_conversations(self):
        return list(self.paginate("/conversations"))

    # --- export job --------------------------------------------------------
    def create_export(self, cv_id, start="2020-01-01T00:00:00Z", end=None):
        if end is None:
            # subtract a safety buffer so 'end' is never in the future from
            # SuperChat's perspective (clock skew / rounding -> 400 otherwise)
            now = datetime.now(timezone.utc) - timedelta(minutes=5)
            end = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        resp = self._request(
            "POST",
            f"/conversations/{cv_id}/export",
            json={"start": start, "end": end},
            headers={"Content-Type": "application/json"},
        )
        if resp.status_code >= 400:
            # empty conversation (no messages in range) is an expected case in a
            # real account - signal it distinctly so the caller can skip, not fail
            if "Keine Nachrichten" in resp.text or "no messages" in resp.text.lower():
                log.info("skip %s: conversation has no messages", cv_id)
                return "empty"
            log.error("export create failed for %s: %s %s", cv_id,
                      resp.status_code, resp.text[:300])
            return None
        return resp.json()

    def get_export(self, cv_id, export_id):
        resp = self._request(
            "GET", f"/conversations/{cv_id}/export/{export_id}"
        )
        resp.raise_for_status()
        return resp.json()

    def wait_for_export(self, cv_id, export_id, timeout=180, interval=2.0):
        """Poll until status == 'done'. Returns link url or None."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            data = self.get_export(cv_id, export_id)
            status = data.get("status")
            if status == "done":
                return (data.get("link") or {}).get("url")
            if status == "failed":
                log.error("export job failed for %s", cv_id)
                return None
            time.sleep(interval)
        log.error("export job timed out for %s", cv_id)
        return None

    def download(self, url, dest_path):
        resp = self._request("GET", url, stream=True)
        resp.raise_for_status()
        with open(dest_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                fh.write(chunk)
        return dest_path
