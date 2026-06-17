"""Parse a SuperChat conversation-export PDF into structured, role-tagged data.

Verified against real exports: multi-line messages (joined to flowing text),
a 1329-char message spanning a page break (footer in the middle), text+attachment,
varying sender names, and dates inside the body (not mistaken for timestamps).

Role is ALWAYS derived from 'Received' (contact) / 'Sent' (agent), never the name.
"""
import re
import subprocess

MS_RE = re.compile(r'^(?P<sender>.*?)\s+ID:\s*(?P<msg_id>ms_[A-Za-z0-9]+)\s*$')
TS_RE = re.compile(
    r'^(?P<dir>Received|Sent)\s+(?P<ts>\d{2}\.\d{2}\.\d{4}\s+\d{2}:\d{2})\s*$'
)
ATT_RE = re.compile(
    r'^Attachments:\s*(?P<name>.+?)\s+ID:\s*(?P<file_id>fi_[A-Za-z0-9]+)\s*$'
)
FOOTER_RE = re.compile(r'Export ID:\s*cex_|Page \d+ of \d+')
CONV_ID_RE = re.compile(r'Conversation\s+ID:\s*(cv_[A-Za-z0-9]+)')
# "Name (handle/email)" -> capture name and the parenthetical
PAREN_RE = re.compile(r'^(?P<name>.*?)\s*\((?P<paren>.*)\)\s*$')


def extract_text(pdf_path):
    out = subprocess.run(
        ["pdftotext", "-layout", "-enc", "UTF-8", pdf_path, "-"],
        capture_output=True, text=True, check=True,
    )
    return out.stdout


def _split_name(raw):
    """'Tester Tester (Inkognito- Seeadler)' -> ('Tester Tester', 'Inkognito- Seeadler')"""
    m = PAREN_RE.match(raw.strip())
    if m:
        return m.group("name").strip(), m.group("paren").strip()
    return raw.strip(), ""


def parse(pdf_path):
    text = extract_text(pdf_path)
    raw_lines = text.split("\n")

    lines = []
    for ln in raw_lines:
        ln = ln.replace("\f", "").rstrip()
        if not ln.strip():
            continue
        if FOOTER_RE.search(ln):
            continue
        lines.append(ln.strip())

    # --- header: participants + conversation id ---------------------------
    conversation_id = None
    contact_name = agent_name = agent_email = None
    for idx, ln in enumerate(lines):
        m = CONV_ID_RE.search(ln)
        if m and not conversation_id:
            conversation_id = m.group(1)
        if ln == "Contact" and idx + 1 < len(lines):
            nm, _ = _split_name(lines[idx + 1])
            contact_name = nm
        if ln == "Superchat User" and idx + 1 < len(lines):
            nm, paren = _split_name(lines[idx + 1])
            agent_name = nm
            if "@" in paren:
                agent_email = paren

    messages = []
    current = None
    body_buf = []
    seen_first_msg = False

    def flush_body():
        if current is not None:
            joined = " ".join(body_buf)
            current["text"] = re.sub(r"\s+", " ", joined).strip()

    for ln in lines:
        m_ms = MS_RE.match(ln)
        m_ts = TS_RE.match(ln)
        m_att = ATT_RE.match(ln)

        if m_ms:
            seen_first_msg = True
            flush_body()
            current = {
                "message_id": m_ms.group("msg_id"),
                "sender": m_ms.group("sender").strip(),
                "direction": None,
                "role": None,
                "timestamp": None,
                "text": "",
                "attachments": [],
            }
            body_buf = []
            messages.append(current)
        elif m_att and current is not None:
            current["attachments"].append({
                "file_id": m_att.group("file_id"),
                "filename": m_att.group("name").strip(),
            })
        elif m_ts and current is not None:
            flush_body()
            direction = m_ts.group("dir").lower()
            current["direction"] = direction
            current["role"] = "contact" if direction == "received" else "agent"
            current["timestamp"] = m_ts.group("ts")
            current = None
            body_buf = []
        elif seen_first_msg and current is not None:
            body_buf.append(ln)
    flush_body()

    # known real names worth anonymizing (drop generic placeholders)
    known_names = []
    for nm in (contact_name, agent_name):
        if nm and nm.lower() not in ("unknown contact", ""):
            known_names.append(nm)
    if agent_email:
        known_names.append(agent_email)

    return {
        "source_pdf": pdf_path.split("/")[-1],
        "conversation_id": conversation_id,
        "contact_name": contact_name,
        "agent_name": agent_name,
        "agent_email": agent_email,
        "known_names": known_names,
        "message_count": len(messages),
        "messages": messages,
    }


def find_pdf(cv_dir):
    """Return the single conversation PDF inside a raw/<cv_id>/ dir."""
    from pathlib import Path
    pdfs = list(Path(cv_dir).glob("*.pdf"))
    return str(pdfs[0]) if pdfs else None
