"""Phase 2a - Import conversations 1:1 into Chatwoot (REAL data, no anonymization).

For each conversation: ensure contact (deduped via SuperChat ct_id as identifier),
create conversation (source_id = cv_id for idempotency), then create each message
as incoming (customer) / outgoing (agent). Optional --with-attachments re-uploads
files from raw/<cv>/attachments/.

Run: python -m src.to_chatwoot [--with-attachments]
"""
import json
import logging
import sys
import tempfile
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
    _BERLIN = ZoneInfo("Europe/Berlin")
except Exception:  # pragma: no cover
    _BERLIN = None

from . import config
from . import parse_pdf
from .chatwoot_client import ChatwootClient
from .collect_attachments import _pdf_preview

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("to_chatwoot")

STATUS_MAP = {"open": "open", "snoozed": "snoozed", "done": "resolved"}


def _iso_ts(ts):
    """SuperChat 'dd.mm.yyyy hh:mm' (Berlin local time) -> ISO8601 with offset.

    Sent to Chatwoot as external_created_at; a Rails post-step copies it into
    the real created_at. Offset-aware so the original instant is preserved and
    Chatwoot displays the correct local time (handles DST).
    """
    if not ts:
        return None
    try:
        dt = datetime.strptime(ts.strip(), "%d.%m.%Y %H:%M")
    except (ValueError, AttributeError):
        return None
    if _BERLIN:
        dt = dt.replace(tzinfo=_BERLIN)
    return dt.isoformat()


def _load_json(path, default):
    if Path(path).exists():
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception:
            pass
    return default


def _index_contacts(contacts):
    return {c["id"]: c for c in contacts if c.get("id")}


def _contact_name(c):
    """Best-effort display name from a SuperChat contact object.
    Tries structured first/last, then common single-name fields, then the
    live_chat handle value (SuperChat's friendly name for anonymous contacts,
    e.g. "Anonymer Oregano"). Returns "" if nothing usable (caller falls back
    to the PDF-parsed contact name)."""
    name = " ".join(p for p in [c.get("first_name"), c.get("last_name")] if p).strip()
    if not name:
        for key in ("name", "full_name", "display_name", "nickname"):
            v = (c.get(key) or "").strip()
            if v:
                name = v
                break
    if not name:
        handles = c.get("handles") or []
        # prefer a live_chat handle, otherwise the first handle with a value
        live = next((h for h in handles if h.get("type") == "live_chat"
                     and (h.get("value") or "").strip()), None)
        h = live or next((h for h in handles if (h.get("value") or "").strip()), None)
        if h:
            name = h["value"].strip()
    return name


def _find_attachment(cv_dir, file_id):
    base = Path(cv_dir) / "attachments" / file_id
    if base.is_dir():
        files = list(base.iterdir())
        if files:
            return str(files[0])
    return None


def reassign(client, agents_map, conv_meta):
    """Re-set the assignee for already-imported conversations (after agents
    were confirmed). Uses chatwoot_state.json -> chatwoot_conversation_id."""
    state = _load_json(config.CHATWOOT_STATE_FILE, {})
    done = 0
    for cv_id, st in state.items():
        conv_cw = st.get("chatwoot_conversation_id")
        if not conv_cw:
            continue
        meta = conv_meta.get(cv_id, {})
        assigned = meta.get("assigned_users") or []
        agent_email = assigned[0].get("email") if assigned else None
        if not agent_email:
            continue
        aid = agents_map.get(agent_email.lower())
        if aid:
            client.add_inbox_member(aid)
            if client.assign_conversation(conv_cw, aid):
                done += 1
                log.info("Reassigned conversation %s -> agent %s", conv_cw, agent_email)
            else:
                log.info("Could not reassign conversation %s (agent %s)",
                         conv_cw, agent_email)
        else:
            log.info("Agent %s not found for conversation %s", agent_email, conv_cw)
    log.info("Reassign done: %d conversations", done)


def main(with_attachments=False, only=None, reassign_only=False):
    config.ensure_dirs()
    if not config.chatwoot_ok():
        log.error("Chatwoot config incomplete (BASE_URL/TOKEN/ACCOUNT_ID/INBOX_ID) "
                  "- skipping Chatwoot import.")
        return

    contacts = _load_json(config.RAW_DIR / "contacts.json", [])
    conversations = _load_json(config.RAW_DIR / "conversations.json", [])
    if not conversations:
        log.error("No conversations.json - run extract first.")
        return

    by_id = _index_contacts(contacts)
    conv_meta = {c["id"]: c for c in conversations if c.get("id")}
    state = _load_json(config.CHATWOOT_STATE_FILE, {})

    client = ChatwootClient()
    # archive inbox must not auto-distribute conversations to all members
    client.disable_auto_assignment()
    # map agent email -> chatwoot agent id (for assignment)
    agents_map = {}
    for a in client.list_agents():
        if a.get("email"):
            agents_map[a["email"].lower()] = a.get("id")

    # agent email -> access token, so outgoing messages are posted as the agent
    agent_tokens = {}
    if config.CHATWOOT_AGENT_TOKENS_PATH:
        raw = _load_json(config.CHATWOOT_AGENT_TOKENS_PATH, {})
        agent_tokens = {k.lower(): v for k, v in raw.items() if v}
        log.info("Loaded %d agent token(s)", len(agent_tokens))

    if reassign_only:
        reassign(client, agents_map, conv_meta)
        return

    done = failed = skipped = 0
    inbox_members_added = set()

    for cv_dir in sorted(d for d in config.RAW_DIR.iterdir() if d.is_dir()):
        cv_id = cv_dir.name
        if only and cv_id != only:
            continue
        if state.get(cv_id, {}).get("status") == "done":
            skipped += 1
            continue

        meta = conv_meta.get(cv_id, {})
        ct_id = (meta.get("contacts") or [{}])[0].get("id")
        contact_obj = by_id.get(ct_id, {})
        name = _contact_name(contact_obj)

        pdf = parse_pdf.find_pdf(cv_dir)
        if not pdf:
            log.warning("No PDF for %s, skipping", cv_id)
            continue
        parsed = parse_pdf.parse(pdf)

        # fall back to the name from the PDF header (the name SuperChat shows in
        # the conversation), then to "Unknown" as a last resort
        if not name:
            pdf_name = (parsed.get("contact_name") or "").strip()
            if pdf_name and pdf_name.lower() != "unknown contact":
                name = pdf_name
        if not name:
            name = "Unknown"

        try:
            contact_cw = client.ensure_contact(
                name, identifier=ct_id or cv_id,
            )
            if not contact_cw:
                raise RuntimeError("contact creation failed")

            conv_status = STATUS_MAP.get(meta.get("status", "open"), "open")
            conv_cw = client.create_conversation(
                contact_cw, source_id=cv_id, status=conv_status
            )
            if not conv_cw:
                raise RuntimeError("conversation creation failed")

            # assign the agent (assigned_user from SuperChat) to the conversation
            assigned = meta.get("assigned_users") or []
            agent_email = (assigned[0].get("email") if assigned
                           else parsed.get("agent_email"))
            if agent_email:
                aid = agents_map.get(agent_email.lower())
                if not aid and config.CHATWOOT_CREATE_AGENTS:
                    aid = client.create_agent(parsed.get("agent_name"), agent_email)
                    if aid:
                        agents_map[agent_email.lower()] = aid
                if aid:
                    # ensure agent is a member of the inbox (needed for assignment
                    # & posting; independent of confirmed status), only once
                    if aid not in inbox_members_added:
                        client.add_inbox_member(aid)
                        inbox_members_added.add(aid)
                    client.assign_conversation(conv_cw, aid)
                else:
                    log.info("Agent %s not in Chatwoot - conversation %s left "
                             "unassigned", agent_email, conv_cw)

            preview_dir = tempfile.mkdtemp(prefix="cw_prev_")
            for msg in parsed["messages"]:
                mtype = "incoming" if msg["role"] == "contact" else "outgoing"
                content = msg["text"]

                # outgoing messages: post as the agent (their token) so the
                # sender is correct. incoming = contact, stays on admin token.
                token = None
                if mtype == "outgoing" and agent_email:
                    token = agent_tokens.get(agent_email.lower())
                    if not token and agent_tokens:
                        log.warning("no token for agent %s - message posted as "
                                    "admin", agent_email)

                att_files = []
                if with_attachments:
                    for a in msg["attachments"]:
                        ap = _find_attachment(cv_dir, a["file_id"])
                        if not ap:
                            continue
                        # for PDFs: prepend a first-page preview image so it
                        # shows inline in Chatwoot, with the PDF as download
                        if ap.lower().endswith(".pdf"):
                            prev = _pdf_preview(ap, Path(preview_dir),
                                                Path(ap).stem)
                            if prev:
                                att_files.append(str(Path(preview_dir) / prev))
                        att_files.append(ap)

                ext_ts = _iso_ts(msg.get("timestamp"))

                if att_files:
                    client.create_message_with_files(
                        conv_cw, content, mtype, att_files, token=token,
                        external_created_at=ext_ts)
                else:
                    if not content and msg["attachments"]:
                        names = ", ".join(a["filename"] for a in msg["attachments"])
                        content = f"[Anhang: {names}]"
                    if content:
                        client.create_message(conv_cw, content, mtype, token=token,
                                              external_created_at=ext_ts)

            state[cv_id] = {"status": "done", "chatwoot_conversation_id": conv_cw}
            done += 1
            # set final status AFTER all messages (Chatwoot ignores it on create
            # and an incoming message would reopen a resolved conversation)
            if conv_status != "open":
                client.set_status(conv_cw, conv_status)
            log.info("Imported %s -> conversation %s (%d msgs)",
                     cv_id, conv_cw, len(parsed["messages"]))
        except Exception as exc:
            state[cv_id] = {"status": "failed", "reason": str(exc)}
            failed += 1
            log.error("Import failed for %s: %s", cv_id, exc)

        config.CHATWOOT_STATE_FILE.write_text(
            json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    log.info("Chatwoot import done. %d ok / %d failed / %d skipped",
             done, failed, skipped)


if __name__ == "__main__":
    only = None
    if "--only" in sys.argv:
        idx = sys.argv.index("--only")
        if idx + 1 < len(sys.argv):
            only = sys.argv[idx + 1]
    main(
        with_attachments="--with-attachments" in sys.argv,
        only=only,
        reassign_only="--reassign" in sys.argv,
    )
