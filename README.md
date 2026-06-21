# Website Scraper Agent

An AI-powered agent that scrapes websites and answers questions about their content using Groq LLMs and the Model Context Protocol (MCP).

## Architecture

```
User
 ├── Chat UI (src/frontend/index.html)  — floating chat widget
 └── POST /chat or /chat/stream  ─┐
                                  ▼
                    FastAPI Server (src/api/app.py)
                                  │
                                  ▼
                    Agent (src/agent/agent.py)
                          │              │
                          ▼              ▼
              MCP Server ──────────  Groq LLM
           (src/mcp_server/server.py) (src/agent/groq_agent.py)
                  │
          ┌───────┼────────┐
          ▼       ▼        ▼
     fetch_pages scrape_bs4 scrape_playwright
```

### Components

| Layer | Location | Role |
|---|---|---|
| **Frontend** | `src/frontend/index.html` | Single embeddable modal UI for any website (Vanilla HTML/CSS/JS, WordPress, etc.). Floating chat widget with Markdown rendering and SSE streaming. |
| **API** | `src/api/app.py` | FastAPI server — `POST /chat` (JSON) and `POST /chat/stream` (SSE) |
| **Agent** | `src/agent/agent.py` | Orchestrator: calls MCP tools, feeds scraped content to LLM |
| **LLM Client** | `src/agent/groq_agent.py` | Groq chat completions (non-streaming and streaming) |
| **MCP Server** | `src/mcp_server/server.py` | FastMCP server exposing 3 scraping tools |
| **Tools** | `src/tools/` | `fetch_website_pages`, `scrape_website_bs4`, `scrape_website_playwright` |

### MCP Tools

| Tool | Backend | Description |
|---|---|---|
| `fetch_pages` | `requests` + BeautifulSoup | Discovers all internal links from a homepage |
| `scrape_bs4` | `requests` + BeautifulSoup | Scrapes a page (fast, no JS) |
| `scrape_playwright` | Playwright Chromium | Scrapes a page (renders JavaScript) |

## Setup

```bash
# Install dependencies
uv sync

# Install Playwright browsers
uv run playwright install chromium

# Create .env
cp .env.example .env
# Edit .env — add your GROQ_API_KEY
```

## Run

```bash
# Start the server
uv run uvicorn src.api.app:app --reload
```

Open `http://localhost:8000/src/frontend/index.html` for the chat UI.

### Deployment

The frontend (`src/frontend/index.html`) is a self-contained, embeddable modal UI. It is **not** WordPress-specific — you can paste the entire file into any HTML page, WordPress Code Snippet, or static site. The only requirement is that the FastAPI backend is reachable from the browser.

When deploying for a client, change **one constant** inside `src/frontend/index.html`:

```js
const PRODUCTION_API_BASE_URL = "https://api.example.com";
```

Replace `https://api.example.com` with your production backend URL.

Local development works automatically — when served from `localhost` or `127.0.0.1`, the frontend uses `http://127.0.0.1:8000` without any configuration changes.

> **Note:** The FastAPI backend must still run separately from WordPress or any static website. The frontend is just a client-side UI; the backend handles scraping and LLM inference.

The old full-page `agent.html` UI has been merged into `index.html`, which now serves as the single frontend for all use cases.

### API endpoints

```bash
# Non-streaming
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"url": "https://books.toscrape.com", "question": "Summarize this site"}'

# Streaming (SSE)
curl -N -X POST http://localhost:8000/chat/stream \
  -H "Content-Type: application/json" \
  -d '{"url": "https://books.toscrape.com", "question": "Summarize this site"}'
```

## Logging

Structured logging is output to the terminal — you can see every MCP tool call and LLM interaction:

```
[MCP CALL] tool=fetch_pages args={'url': 'https://books.toscrape.com'}
[MCP OK]   tool=fetch_pages result=[4 pages found]
[MCP CALL] tool=scrape_bs4 args={'url': 'https://books.toscrape.com'}
[MCP OK]   tool=scrape_bs4 result={'url': '...', 'title': 'All products...', ...}
[LLM] Streaming answer: model=openai/gpt-oss-120b page_text_length=1851
[LLM] Streaming finished: 2161 chars total
[AGENT] Finished: scraper=bs4 page=https://books.toscrape.com
```
