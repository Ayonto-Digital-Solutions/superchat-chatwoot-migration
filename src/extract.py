"""Phase 1 - Extract everything from SuperChat into raw/.

Pulls contacts + conversations, then per conversation runs an export job,
downloads the ZIP and unpacks it. Resumable via state.json. Never aborts on a
single failure (logs + continues).

Run: python -m src.extract
"""
import json
import logging
import zipfile

from . import config
from .superchat_client import SuperChatClient

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("extract")


def _load_state():
    if config.STATE_FILE.exists():
        try:
            return json.loads(config.STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            log.warning("state.json unreadable, starting fresh")
    return {}


def _save_state(state):
    config.STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def main():
    config.ensure_dirs()
    if not config.superchat_ok():
        log.error("SUPERCHAT_API_KEY missing - skipping extract phase.")
        return

    client = SuperChatClient()

    # 0) identity check (non-fatal)
    try:
        me = client.get_me()
        ws = (me.get("workspace") or {}).get("name")
        log.info("Authenticated. Workspace: %s", ws)
    except Exception as exc:
        log.warning("Could not verify identity (continuing): %s", exc)

    # 1) contacts
    try:
        contacts = client.list_contacts()
        (config.RAW_DIR / "contacts.json").write_text(
            json.dumps(contacts, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Saved %d contacts", len(contacts))
    except Exception as exc:
        log.error("Failed to list contacts: %s", exc)

    # 2) conversations (metadata)
    try:
        conversations = client.list_conversations()
        (config.RAW_DIR / "conversations.json").write_text(
            json.dumps(conversations, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        log.info("Found %d conversations", len(conversations))
    except Exception as exc:
        log.error("Failed to list conversations: %s", exc)
        return

    # 3) per-conversation export
    state = _load_state()
    done = failed = skipped = 0
    for conv in conversations:
        cv_id = conv.get("id")
        if not cv_id:
            continue
        if state.get(cv_id, {}).get("status") == "done":
            skipped += 1
            continue

        log.info("Exporting %s ...", cv_id)
        export = client.create_export(cv_id)
        if export == "empty":
            # conversation has no messages -> mark done so it's not retried
            state[cv_id] = {"status": "done", "reason": "empty"}
            skipped += 1
            _save_state(state)
            continue
        if not export or not export.get("id"):
            state[cv_id] = {"status": "failed", "reason": "create_export"}
            failed += 1
            _save_state(state)
            continue

        export_id = export["id"]
        url = export.get("link", {}).get("url") if export.get("status") == "done" else None
        if not url:
            url = client.wait_for_export(cv_id, export_id)
        if not url:
            state[cv_id] = {"status": "failed", "reason": "no_link", "cex": export_id}
            failed += 1
            _save_state(state)
            continue

        try:
            zip_path = config.RAW_DIR / f"{cv_id}.zip"
            client.download(url, zip_path)
            target = config.RAW_DIR / cv_id
            target.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path) as zf:
                zf.extractall(target)
            zip_path.unlink(missing_ok=True)
            state[cv_id] = {"status": "done", "cex": export_id}
            done += 1
            log.info("  unpacked -> raw/%s/", cv_id)
        except Exception as exc:
            state[cv_id] = {"status": "failed", "reason": str(exc), "cex": export_id}
            failed += 1
            log.error("  download/unzip failed for %s: %s", cv_id, exc)
        _save_state(state)

    log.info("Extract done. %d ok / %d failed / %d skipped", done, failed, skipped)


if __name__ == "__main__":
    main()
