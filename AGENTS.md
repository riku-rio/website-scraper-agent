# AGENTS.md — Website Scraper Agent

## Dev commands (use `uv` not pip)

| Action | Command |
|---|---|
| Install deps | `uv sync` |
| Run server | `uv run uvicorn src.api.app:app --reload` |
| Run CLI agent | `uv run python main.py` |
| Install Playwright | `uv run playwright install chromium` |
| Single tool test | `uv run python -c "from src.tools.scrape_website_bs4 import scrape_website_bs4; print(scrape_website_bs4('https://books.toscrape.com'))"` |

No test framework, linter, or formatter is configured. Don't add one unless asked.

## Architecture

- **`src/api/app.py`** — FastAPI entrypoint (uvicorn target). Configures `logging.basicConfig` on import. Two endpoints: `POST /chat` (JSON) and `POST /chat/stream` (SSE).
- **`src/agent/agent.py`** — Orchestrator. Spawns MCP server as subprocess, calls its tools, then feeds scraped content to Groq LLM.
- **`src/agent/groq_agent.py`** — Groq chat completions. Calls `load_dotenv()` at module level. `GROQ_MODEL` defaults to `"openai/gpt-oss-120b"`. Page text is truncated to 12K chars.
- **`src/mcp_server/server.py`** — FastMCP server with 3 tools: `fetch_pages`, `scrape_bs4`, `scrape_playwright`.
- **`src/tools/`** — Low-level implementations (requests+BS4, Playwright).
- **`src/frontend/index.html`** — Single embeddable modal UI for any website (Vanilla HTML/CSS/JS, WordPress, etc.). Sets `API_BASE_URL` based on `location.hostname` — uses `http://127.0.0.1:8000` automatically on localhost, and reads the production URL from `PRODUCTION_API_BASE_URL` elsewhere. The old `agent.html` full-page features have been merged here.

## Critical gotchas

- **MCP server runs as a subprocess over stdio.** Do NOT add `print()` or logging to `src/mcp_server/server.py` — it would corrupt the JSON-RPC protocol. All observability goes in `call_mcp_tool()` on the *caller* side.
- **`call_mcp_tool()` in `agent.py`** is the single chokepoint for every MCP tool invocation. Instrument it when you need logging, metrics, or error handling.
- **scrape_bs4 fallback:** `agent.py` silently falls back to `scrape_playwright` on any exception. The first error is swallowed — don't lose it if debugging.
- **`.env` is gitignored.** Use `.env.example` as the template. `load_dotenv()` is called in `groq_agent.py`.
- **Python 3.10.11 only** (see `.python-version`).
- **Frontend API base URL:** Change `PRODUCTION_API_BASE_URL` in `src/frontend/index.html` when deploying to production. Localhost is detected automatically.
- **`logging.basicConfig` runs in `app.py`.** Running `main.py` directly won't produce the same formatted logs.
