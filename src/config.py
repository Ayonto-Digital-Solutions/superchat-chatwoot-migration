"""Central configuration loaded from environment / .env file.

No hard failures here: missing values are reported by the phase that needs
them, so the app can still run the phases that ARE configured.
"""
import os
from pathlib import Path

# Project root = parent of src/
ROOT = Path(__file__).resolve().parent.parent

RAW_DIR = ROOT / "raw"
OUT_DIR = ROOT / "out"
STATE_FILE = ROOT / "state.json"
CHATWOOT_STATE_FILE = ROOT / "chatwoot_state.json"


def _load_dotenv():
    """Minimal .env loader (no external dependency)."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        os.environ.setdefault(key, val)


_load_dotenv()

# --- SuperChat ---
SUPERCHAT_API_KEY = os.environ.get("SUPERCHAT_API_KEY", "")
SUPERCHAT_BASE_URL = os.environ.get(
    "SUPERCHAT_BASE_URL", "https://api.superchat.com/v1.0"
).rstrip("/")

# --- Chatwoot ---
CHATWOOT_BASE_URL = os.environ.get("CHATWOOT_BASE_URL", "").rstrip("/")
CHATWOOT_API_TOKEN = os.environ.get("CHATWOOT_API_TOKEN", "")
CHATWOOT_ACCOUNT_ID = os.environ.get("CHATWOOT_ACCOUNT_ID", "")
CHATWOOT_INBOX_ID = os.environ.get("CHATWOOT_INBOX_ID", "")
# Create the SuperChat agent (with its original email) in Chatwoot if missing,
# then assign the conversation to it. Note: Chatwoot sends an invitation email
# when a new agent is created.
CHATWOOT_CREATE_AGENTS = (
    os.environ.get("CHATWOOT_CREATE_AGENTS", "true").lower() == "true"
)
# JSON file mapping agent email -> Chatwoot access token, so OUTGOING messages
# are posted as the actual agent (sender). Without it, agent messages appear as
# the admin token owner. Export tokens from Chatwoot (see README).
CHATWOOT_AGENT_TOKENS_PATH = os.environ.get("CHATWOOT_AGENT_TOKENS_PATH", "")


def ensure_dirs():
    for d in (RAW_DIR, OUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def superchat_ok():
    return bool(SUPERCHAT_API_KEY)


def chatwoot_ok():
    return all(
        [CHATWOOT_BASE_URL, CHATWOOT_API_TOKEN, CHATWOOT_ACCOUNT_ID, CHATWOOT_INBOX_ID]
    )
