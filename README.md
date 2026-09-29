# cookt

A self-hosted recipe app for one household. It keeps the family's recipes in one SQLite file, works
offline as an installable web app on phones and iPads, and uses **local** models for the jobs that
need judgment: reading a recipe out of a photo, tagging, and suggesting copy edits. Nothing is sent
to a cloud AI service.

## What it does

- **Library.** Fuzzy, typo-tolerant search that runs in the browser and works offline ("carnitsa"
  finds carnitas), facets for course, cuisine, protein and diet, and favourites. When the server is
  reachable, results from local-embedding semantic search are merged in, so related dishes also
  show up.
- **Recipe and cook mode.** Scale servings, convert units, open several recipes at once in tabs. Cook
  mode shows one step at a time in large type and advances by swipe, tap, arrow keys or a foot pedal.
  It keeps the screen awake, runs several timers at once, and remembers where you left off.
  "For next time" notes appear the next time you cook the dish.
- **Timer alerts on a locked device.** The server sends running timers as Web Push messages when
  they end, so the alert still arrives when an iPad is locked or the app is in the background.
- **Import.** URLs (schema.org JSON-LD and microdata, with a model fallback), pasted text, Paprika
  exports (`.html`, `.zip`, `.paprikarecipes`), cookbook EPUBs, photos of cookbook pages or
  handwritten cards (vision model; lines it is unsure of are flagged for you to confirm), and
  Instagram/TikTok/YouTube links (caption via `yt-dlp`, with an optional local Whisper transcript).
  Imports go to an inbox with progress states, and one tap saves each result. The URL fetcher is
  SSRF-safe.
- **Enrichment.** Automatic tags with the evidence behind them, a check that keeps meat or dairy
  recipes from being tagged vegetarian or vegan, per-serving nutrition from USDA FoodData Central
  (or the publisher's own figures), embeddings, and pairing suggestions. Every automatic change
  shows up in a **Changes** feed and can be reverted with one tap. A reverted tag is never
  suggested again.
- **Review.** A local model proposes copy edits against a house style guide
  ([`docs/style-guide.md`](docs/style-guide.md)). Recipe text changes only when a person approves an
  edit, every approval is saved as a revision, and every decision can be undone.
- **Planning.** A week board (drag recipes between days, set servings), a shopping list that merges
  ingredients across recipes when their units are compatible and groups them by store section,
  pantry staples, "what can I make", and a cook log.
- **Read-only MCP server** at `/mcp`, so an assistant can search and read the catalog, meal plan and
  shopping list. It has no tools that write, and a test checks that calling every tool leaves the
  database byte-for-byte unchanged.

## How it fits together

```
 browser / home-screen PWA ── React 19 + Vite, service worker, MiniSearch (offline)
            │
            ▼
 FastAPI (uvicorn) ── serves the built PWA, /api, /mcp; one in-process job worker
            │
            ├── data/cookt.db        SQLite (WAL): recipes, revisions, tags, plans, jobs
            ├── data/images/         recipe photos + thumbnails
            └── local models (optional, OpenAI-compatible HTTP)
                  ├── chat + vision   e.g. llama-swap / llama.cpp server   (LLAMA_BASE_URL)
                  ├── embeddings      e.g. Qwen3-Embedding server          (EMBEDDING_BASE_URL)
                  └── speech-to-text  e.g. faster-whisper server           (WHISPER_BASE_URL)
```

All model calls go through `cookt.llm`, which takes a file lock (`data/llm.lock`), so the app and the
backfill scripts never send overlapping requests to a single-slot model server.

## Requirements

- Python 3.12 and [uv](https://docs.astral.sh/uv/)
- Node.js 20 or newer, to build the frontend
- `sqlite3` CLI, for the backup scripts
- Optional: an OpenAI-compatible local model server for model-based import, tagging, copy edits and
  store-section grouping. Without one, structured imports (JSON-LD, microdata, Paprika) and
  everything else still work.
- Optional: `yt-dlp` on `PATH` for social video imports.

## Quick start

```bash
uv sync
(cd frontend && npm ci && npm run build)
uv run python -m cookt                      # http://127.0.0.1:8088
```

On first start the app creates `data/cookt.db`. Import recipes from the **Inbox** page.

For nutrition, download and load the USDA datasets once ([`docs/fdc.md`](docs/fdc.md)):

```bash
uv run python scripts/load_fdc.py
```

Then fill in tags, nutrition, embeddings and pairing features for recipes already in the library.
The backfill can be resumed if it stops:

```bash
uv run python -m cookt.enrich backfill
uv run python -m cookt.enrich status
```

New imports are enriched automatically by the job worker.

## Configuration

Settings are read from the environment ([`backend/cookt/config.py`](backend/cookt/config.py)).
The ones you are most likely to change:

| Variable | Default | Purpose |
|---|---|---|
| `COOKT_HOST` / `COOKT_PORT` | `0.0.0.0` / `8088` | Listen address |
| `COOKT_DATA_DIR` | `./data` | Database, images, uploads, push key, backups |
| `COOKT_PIN` | *(unset)* | Optional shared PIN for non-loopback clients (see Security below) |
| `COOKT_VAPID_SUBJECT` | `mailto:cookt@example.com` | Web Push contact (`https:` URL or `mailto:`). Apple rejects placeholder hosts, so set it to your own for iPad alerts |
| `LLAMA_BASE_URL` | `http://127.0.0.1:8080/v1` | Chat and vision model server |
| `COOKT_TEXT_MODEL` / `COOKT_VISION_MODEL` / `COOKT_COPYEDIT_MODEL` | see `config.py` | Model names on that server |
| `EMBEDDING_BASE_URL` / `EMBEDDING_MODEL` | `http://127.0.0.1:8111/v1` / `qwen3-embedding-0.6b` | Embedding server |
| `WHISPER_BASE_URL` / `WHISPER_MODEL` | `http://127.0.0.1:8000/v1` / `Systran/faster-whisper-small` | Optional transcription for social videos |
| `COOKT_RUN_WORKER` | `1` | Run the import/enrichment job worker in the server process |

The frontend reads one optional build-time variable, `VITE_SECURE_URL`. It is the HTTPS address the
Settings page links to when someone opens the app over plain HTTP. Put it in
`frontend/.env.local`, which git ignores.

## Running it for a household

- **HTTPS.** Browsers allow service workers, and therefore offline use and Home Screen installs
  that work offline, only in a secure context. Put the app behind HTTPS, for example
  `tailscale serve` or a reverse proxy, and install it on devices from that address.
- **Service.** [`deploy/cookt-household.service`](deploy/cookt-household.service) is a systemd user
  unit. Put machine-specific settings in a drop-in (`systemctl --user edit cookt-household.service`)
  so they stay out of the repo. [`scripts/demo.sh`](scripts/demo.sh) runs `start`, `stop`,
  `restart`, `status` and `logs` against that unit.
- **Backups.** [`scripts/backup.sh`](scripts/backup.sh) takes a consistent SQLite `.backup`, tars
  the images and copies the Web Push key. [`scripts/restore-verify.sh`](scripts/restore-verify.sh)
  restores the newest backup into a temporary directory and checks it. `deploy/cookt-backup.*` runs
  both nightly and keeps 14 backups. If you lose `data/vapid.pem`, every device has to turn timer
  alerts on again.
- **iPhone/iPad sharing.** iOS Safari does not support the Web Share Target API. Use the Shortcut in
  [`docs/ios-shortcut.md`](docs/ios-shortcut.md) to send links, text and photos to the inbox.

### Security

cookt is built for a private network, such as a tailnet or home LAN, and has no user accounts.
`COOKT_PIN` adds a light shared-PIN gate for non-loopback clients. Loopback requests always
bypass the PIN, so a reverse proxy on the same host must forward the real client address
(`X-Forwarded-For`), or the PIN does nothing. Do not expose the app to the public internet as it is.

## Development

```bash
uv run pytest                                        # backend; model/network tests are opt-in markers
cd frontend && ./node_modules/.bin/vitest run        # frontend unit tests
cd frontend && npm run dev                           # Vite dev server on 127.0.0.1:5180
cd frontend && npx playwright test --project=chromium  # browser acceptance tests; server must be running (COOKT_URL to point elsewhere)
```

- Design tokens live in one file, [`frontend/src/styles/tokens.css`](frontend/src/styles/tokens.css)
  (light and dark). Styles are component-scoped CSS modules with no `!important`.
- Recipe ingredient and step text is never rewritten automatically. Automatic changes are limited
  to tags and derived metadata, are recorded in `changes`, and can be reverted.
- The MCP server must stay read-only. [`backend/tests/test_mcp.py`](backend/tests/test_mcp.py)
  enforces this.
- `backend/cookt/migrate/` and the `scripts/eval_*.py` scripts are one-off tooling. They moved the
  author's recipes from two earlier apps into cookt and measured import and tagging quality
  against that catalog. They need local snapshots that are not part of this repository.

## Layout

| Path | What |
|---|---|
| `backend/cookt/` | FastAPI app, SQLite schema, local-model client, MCP server, Web Push |
| `backend/cookt/extraction/` | Recipe parsing: JSON-LD, microdata, Paprika, EPUB, plain text, model and vision fallbacks |
| `backend/cookt/enrich/` | Tagging, nutrition (USDA FDC), embeddings, pairing features |
| `backend/cookt/copyedit.py` | Copy-edit proposals for the Review page |
| `backend/tests/` | pytest, including the parser regression corpus |
| `frontend/` | React + Vite PWA, service worker template, Playwright tests |
| `scripts/` | Service control, backup/restore, FDC loader, evals |
| `deploy/` | systemd user units |
| `data/` | Runtime data (gitignored) |
