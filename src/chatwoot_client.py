"""Chatwoot Application API client (account-scoped).

Docs: /api/v1/accounts/{account_id}/...  with header `api_access_token`.
Used by the Chatwoot 1:1 import (REAL data, no anonymization).
"""
import logging

import requests

from . import config

log = logging.getLogger("chatwoot")


class ChatwootClient:
    def __init__(self):
        self.base = (
            f"{config.CHATWOOT_BASE_URL}/api/v1/accounts/{config.CHATWOOT_ACCOUNT_ID}"
        )
        self.inbox_id = config.CHATWOOT_INBOX_ID
        self.session = requests.Session()
        self.session.headers.update({"api_access_token": config.CHATWOOT_API_TOKEN})

    # --- contacts ---------------------------------------------------------
    def find_contact(self, identifier):
        """Find a contact previously imported with this SuperChat id."""
        try:
            r = self.session.get(
                f"{self.base}/contacts/search", params={"q": identifier}, timeout=60
            )
            r.raise_for_status()
            for c in r.json().get("payload", []):
                if c.get("identifier") == identifier:
                    return c.get("id")
        except requests.RequestException as exc:
            log.warning("contact search failed for %s: %s", identifier, exc)
        return None

    def create_contact(self, name, identifier, extra=None):
        payload = {"name": name or "Unknown", "identifier": identifier}
        if extra:
            payload.update(extra)
        r = self.session.post(f"{self.base}/contacts", json=payload, timeout=60)
        if r.status_code >= 400:
            log.error("create contact failed (%s): %s", r.status_code, r.text[:300])
            return None
        data = r.json().get("payload", {})
        contact = data.get("contact", data)
        return contact.get("id")

    def ensure_contact(self, name, identifier, extra=None):
        cid = self.find_contact(identifier)
        if cid:
            return cid
        return self.create_contact(name, identifier, extra)

    # --- agents ----------------------------------------------------------
    def list_agents(self):
        try:
            r = self.session.get(f"{self.base}/agents", timeout=30)
            r.raise_for_status()
            return r.json()  # list of {id, name, email, role, ...}
        except requests.RequestException as exc:
            log.warning("list agents failed: %s", exc)
            return []

    def create_agent(self, name, email):
        """Adds an agent to the account. NOTE: Chatwoot sends an invitation
        email and creates a real user."""
        payload = {"name": name or email, "email": email, "role": "agent"}
        r = self.session.post(f"{self.base}/agents", json=payload, timeout=30)
        if r.status_code >= 400:
            log.error("create agent failed (%s): %s", r.status_code, r.text[:200])
            return None
        return r.json().get("id")

    def disable_auto_assignment(self):
        """Turn off round-robin auto-assignment on the target inbox. Otherwise
        Chatwoot reassigns imported conversations to all inbox members (and
        sends notification emails), overriding our explicit agent assignment."""
        try:
            r = self.session.patch(
                f"{self.base}/inboxes/{self.inbox_id}",
                json={"enable_auto_assignment": False}, timeout=30,
            )
            if r.status_code >= 400:
                log.warning("disable auto-assignment failed (%s): %s",
                            r.status_code, r.text[:200])
                return False
            return True
        except requests.RequestException as exc:
            log.warning("disable auto-assignment failed: %s", exc)
            return False

    def add_inbox_member(self, user_id):
        """Add an agent to the target inbox. Required so the agent can be set as
        assignee and post messages - works regardless of confirmed status."""
        try:
            r = self.session.post(
                f"{self.base}/inbox_members",
                json={"inbox_id": int(self.inbox_id), "user_ids": [user_id]},
                timeout=30,
            )
            if r.status_code >= 400:
                log.debug("add_inbox_member (%s): %s", r.status_code, r.text[:150])
                return False
            return True
        except (requests.RequestException, ValueError) as exc:
            log.warning("add_inbox_member failed: %s", exc)
            return False

    def assign_conversation(self, conversation_id, assignee_id):
        r = self.session.post(
            f"{self.base}/conversations/{conversation_id}/assignments",
            json={"assignee_id": assignee_id}, timeout=30,
        )
        if r.status_code >= 400:
            log.error("assign conversation failed (%s): %s",
                      r.status_code, r.text[:200])
            return False
        return True

    # --- conversations ----------------------------------------------------
    def create_conversation(self, contact_id, source_id, status="open"):
        payload = {
            "inbox_id": self.inbox_id,
            "contact_id": contact_id,
            "source_id": source_id,
            "status": status,
        }
        r = self.session.post(f"{self.base}/conversations", json=payload, timeout=60)
        if r.status_code >= 400:
            log.error("create conversation failed (%s): %s",
                      r.status_code, r.text[:300])
            return None
        return r.json().get("id")

    def set_status(self, conversation_id, status):
        """Set conversation status (open|resolved|pending|snoozed). Must be done
        AFTER messages are posted - Chatwoot ignores status on create and an
        incoming message reopens a resolved conversation."""
        r = self.session.post(
            f"{self.base}/conversations/{conversation_id}/toggle_status",
            json={"status": status}, timeout=30,
        )
        if r.status_code >= 400:
            log.error("set status failed (%s): %s", r.status_code, r.text[:200])
            return False
        return True

    # --- messages ---------------------------------------------------------
    def create_message(self, conversation_id, content, message_type, token=None,
                       external_created_at=None):
        payload = {
            "content": content,
            "message_type": message_type,  # incoming | outgoing
            "private": False,
        }
        # original SuperChat timestamp; copied into the real created_at by a
        # Rails post-step (Chatwoot sorts/displays by created_at, not this field)
        if external_created_at:
            payload["external_created_at"] = external_created_at
        headers = {"api_access_token": token} if token else None
        r = self.session.post(
            f"{self.base}/conversations/{conversation_id}/messages",
            json=payload, headers=headers, timeout=60,
        )
        if r.status_code >= 400:
            log.error("create message failed (%s): %s", r.status_code, r.text[:200])
            return None
        return r.json().get("id")

    def create_message_with_files(self, conversation_id, content, message_type, file_paths, token=None,
                                  external_created_at=None):
        """Upload one or more files on a single message.
        Order matters: put an inline-renderable image first (e.g. a PDF preview)
        so Chatwoot shows it directly, with the original file as download.
        token: post as this agent (their access token) instead of the default."""
        import mimetypes
        data = {
            "content": content or "",
            "message_type": message_type,
            "private": "false",
        }
        if external_created_at:
            data["external_created_at"] = external_created_at
        headers = {"api_access_token": token} if token else None
        handles, files = [], []
        try:
            for fp in file_paths:
                try:
                    fh = open(fp, "rb")
                except OSError as exc:
                    log.error("attachment read failed (%s): %s", fp, exc)
                    continue
                handles.append(fh)
                fname = fp.split("/")[-1]
                ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
                files.append(("attachments[]", (fname, fh, ctype)))
            if not files:
                return None
            r = self.session.post(
                f"{self.base}/conversations/{conversation_id}/messages",
                data=data, files=files, headers=headers, timeout=120,
            )
            if r.status_code >= 400:
                log.error("create message+files failed (%s): %s",
                          r.status_code, r.text[:200])
                return None
            return r.json().get("id")
        finally:
            for fh in handles:
                fh.close()

    def create_message_with_file(self, conversation_id, content, message_type, file_path, token=None,
                                 external_created_at=None):
        return self.create_message_with_files(
            conversation_id, content, message_type, [file_path], token=token,
            external_created_at=external_created_at,
        )
