# SuperChat → Chatwoot Migration Tool

A one-shot, local Docker CLI tool to export all of your **SuperChat** contacts
and conversations and import them **1:1 into Chatwoot** — including agents,
assignment, timestamps and (optionally) file attachments.

Pure batch app — it **exposes no ports**, runs, does its work and exits, so it
cannot collide with any running container.

## Flow

```
SuperChat API ──(extract)──> raw/ ──(to_chatwoot)──> Chatwoot
                                                     (real data, 1:1)
```

1. **extract** – pulls all contacts + conversations from SuperChat into `raw/`
   (resumable via `state.json`).
2. **to_chatwoot** – recreates every conversation in Chatwoot: contact, agent,
   assignment, incoming/outgoing messages and original timestamps. Optionally
   re-uploads attachments.

## Requirements

- Docker + Docker Compose
- A SuperChat **API key** (`X-API-KEY`)
- A Chatwoot instance with an **API access token**, account ID and target inbox ID

## Setup

```bash
cp .env.example .env      # fill in values (at least SUPERCHAT_API_KEY)
docker compose build
```

### Configuration (`.env`)

| Variable | Required | Description |
|---|---|---|
| `SUPERCHAT_API_KEY` | extract | SuperChat API key (header `X-API-KEY`) |
| `SUPERCHAT_BASE_URL` | – | Defaults to `https://api.superchat.com/v1.0` |
| `CHATWOOT_BASE_URL` | import | e.g. `https://chat.example.com` |
| `CHATWOOT_API_TOKEN` | import | Chatwoot access token of an admin user |
| `CHATWOOT_ACCOUNT_ID` | import | Numeric account ID |
| `CHATWOOT_INBOX_ID` | import | Target inbox ID |
| `CHATWOOT_CREATE_AGENTS` | – | Create missing agents (sends invitation mail!). Default `true` |
| `CHATWOOT_AGENT_TOKENS_PATH` | – | JSON file `email → access_token` so agent messages are posted as the real agent |

## Usage

Either via `make` or directly with `docker compose run`.

```bash
# 1) Pull everything from SuperChat (resumable)
make extract
# docker compose run --rm app python -m src.extract

# 2) Import 1:1 into Chatwoot (real data)
make chatwoot
# docker compose run --rm app python -m src.to_chatwoot

# ...including file attachments
make chatwoot-attach
# docker compose run --rm app python -m src.to_chatwoot --with-attachments

# Optional: collect all attachments into out/attachments/ (with manifest)
make attachments
```

Run `make help` for the full list of commands.

### Posting messages as the real agent

By default, all imported agent messages appear as the owner of
`CHATWOOT_API_TOKEN`. To preserve the original sender per message, create a JSON
file mapping each agent's email to their personal Chatwoot access token and point
`CHATWOOT_AGENT_TOKENS_PATH` at it:

```json
{
  "anna@example.com": "xxxxxxxxxxxxxxxx",
  "ben@example.com":  "yyyyyyyyyyyyyyyy"
}
```

Incoming (customer) messages always use the admin token.

### Re-assigning agents later

If you imported before the new agents confirmed their accounts, re-run the
assignment step (uses `chatwoot_state.json`):

```bash
make reassign
# docker compose run --rm app python -m src.to_chatwoot --reassign
```

## Notes & gotchas (verified against the live API)

- SuperChat auth header is `X-API-KEY`, base URL `https://api.superchat.com/v1.0`.
- Messages are available **only** via per-conversation export jobs — there is no
  list-messages endpoint.
- The export `end` timestamp must **not** be in the future (HTTP 400 otherwise);
  the tool sets it to `now` (UTC) minus a small buffer.
- The finished export status is **`done`** (not `completed`, despite the docs).
- Download links are temporary (~12 h).
- An export is a ZIP containing a PDF (the messages) plus an `attachments/` folder.
- PDF parsing relies on `pdftotext`; attachment previews on `pdftoppm` — both come
  from `poppler-utils`, which is installed in the Docker image.

## Idempotency & resumability

- **extract** tracks progress in `state.json`; finished conversations are skipped
  on re-run.
- **to_chatwoot** tracks progress in `chatwoot_state.json` and uses the SuperChat
  conversation id as Chatwoot `source_id`, so re-runs don't duplicate.

## Security

- `.env`, `agent-tokens.json`, `raw/`, `out/`, `state.json` and
  `chatwoot_state.json` are gitignored. Never commit your API keys.
- Chatwoot receives the **real** conversation data, unmodified.

## How it works

| File | Purpose |
|---|---|
| `src/extract.py` | Phase 1: pull contacts + conversations, run export jobs, download & unpack ZIPs |
| `src/superchat_client.py` | SuperChat API client (pagination, export jobs, retries/backoff) |
| `src/parse_pdf.py` | Parse the conversation-export PDF into role-tagged messages |
| `src/to_chatwoot.py` | Phase 2: recreate contacts, conversations, agents, messages in Chatwoot |
| `src/chatwoot_client.py` | Chatwoot Application API client |
| `src/collect_attachments.py` | Collect attachments into `out/attachments/` with a manifest |
| `src/config.py` | Configuration from `.env` |

## License

MIT — see [LICENSE](LICENSE).
