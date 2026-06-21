import ast
import json
import logging
import os

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.agent.groq_agent import generate_answer, stream_answer

logger = logging.getLogger(__name__)


def extract_tool_text(tool_result) -> str:
    if not tool_result.content:
        return ""

    first_content = tool_result.content[0]

    if hasattr(first_content, "text"):
        return first_content.text

    return str(first_content)


async def call_mcp_tool(session: ClientSession, tool_name: str, arguments: dict):
    logger.info("[MCP CALL] tool=%s args=%s", tool_name, arguments)

    try:
        result = await session.call_tool(tool_name, arguments)
    except Exception as e:
        logger.error("[MCP ERR]  tool=%s - %s: %s", tool_name, type(e).__name__, e)
        raise

    if getattr(result, "structured_content", None) is not None:
        return result.structured_content["result"]

    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent["result"]

    text = extract_tool_text(result)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(text)

    if isinstance(parsed, dict) and "result" in parsed:
        logger.info("[MCP OK]   tool=%s result=%s", tool_name, _summarize(parsed["result"]))
        return parsed["result"]

    logger.info("[MCP OK]   tool=%s result=%s", tool_name, _summarize(parsed))
    return parsed


def _summarize(value, max_len=80) -> str:
    text = str(value)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


def build_pages_to_scrape(user_url: str, discovered_pages: list[str], limit: int = 5) -> list[str]:
    pages = [user_url]
    for page in discovered_pages:
        if page not in pages:
            pages.append(page)
    return pages[:limit]


def _get_max_scrape_pages() -> int:
    """Read MAX_SCRAPE_PAGES from environment, default to 5, fallback on invalid."""
    try:
        value = os.environ.get("MAX_SCRAPE_PAGES", "5")
        return max(1, int(value))
    except (ValueError, TypeError):
        return 5


async def _scrape_page_with_fallback(session: ClientSession, url: str) -> dict | None:
    """Scrape a single page: try bs4 first, fallback to playwright. Return None if both fail."""
    try:
        result = await call_mcp_tool(session, "scrape_bs4", {"url": url})
        return result
    except Exception as e:
        logger.warning("scrape_bs4 failed for %s: %s", url, e)

    try:
        result = await call_mcp_tool(session, "scrape_playwright", {"url": url})
        return result
    except Exception as e:
        logger.error("scrape_playwright also failed for %s: %s", url, e)
        return None


def _build_combined_content(results: list[dict], user_url: str) -> str:
    """Combine multiple scraped pages, clearly separating the primary URL from related pages."""
    parts = []
    related_idx = 0
    for r in results:
        url = r.get('url', '')
        if url == user_url:
            parts.append(
                f"PRIMARY REQUESTED PAGE:\n"
                f"URL: {url}\n"
                f"TITLE: {r.get('title', '')}\n"
                f"CONTENT:\n"
                f"{r.get('text', '')}"
            )
        else:
            related_idx += 1
            parts.append(
                f"RELATED PAGE {related_idx}:\n"
                f"URL: {url}\n"
                f"TITLE: {r.get('title', '')}\n"
                f"CONTENT:\n"
                f"{r.get('text', '')}"
            )
    return "\n\n---\n\n".join(parts)


async def run_agent(question: str, url: str) -> str:
    logger.info("[AGENT] Starting: question=%s url=%s", question, url)

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "src.mcp_server.server"],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            try:
                pages = await call_mcp_tool(
                    session,
                    "fetch_pages",
                    {"url": url},
                )
            except Exception as e:
                logger.exception("fetch_pages failed for %s: %s", url, e)
                pages = [url]

            if not pages:
                pages = [url]

            max_pages = _get_max_scrape_pages()
            pages_to_scrape = build_pages_to_scrape(url, pages, limit=max_pages)

            results = []
            for page_url in pages_to_scrape:
                result = await _scrape_page_with_fallback(session, page_url)
                if result is not None:
                    results.append(result)

            if not results:
                return "I could not scrape any pages from this website."

            combined_text = _build_combined_content(results, url)

            answer = generate_answer(
                question=question,
                combined_content=combined_text,
            )

            logger.info("[AGENT] Finished: %d pages scraped", len(results))

            page_list = "\n".join(f"- {r['url']}" for r in results)

            return f"""
Pages scraped ({len(results)}):
{page_list}

{answer}
            """

async def stream_agent(question: str, url: str):
    logger.info("[AGENT] Starting (stream): question=%s url=%s", question, url)

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "src.mcp_server.server"],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            yield {
                "event": "progress",
                "data": "Fetching related website pages...",
            }

            try:
                pages = await call_mcp_tool(
                    session,
                    "fetch_pages",
                    {"url": url},
                )
            except Exception as e:
                logger.exception("fetch_pages failed for %s: %s", url, e)
                yield {
                    "event": "progress",
                    "data": "Page discovery failed. Scraping the provided URL only.",
                }
                pages = [url]

            if not pages:
                pages = [url]

            max_pages = _get_max_scrape_pages()
            pages_to_scrape = build_pages_to_scrape(url, pages, limit=max_pages)
            total = len(pages_to_scrape)

            related_count = total - 1
            yield {
                "event": "progress",
                "data": f"Found {len(pages)} pages. Scraping requested page plus up to {related_count} related pages...",
            }

            results = []

            for i, page_url in enumerate(pages_to_scrape, 1):
                yield {
                    "event": "progress",
                    "data": f"Scraping {i}/{total}:\n  {page_url}",
                }

                result = await _scrape_page_with_fallback(session, page_url)

                if result is not None:
                    results.append(result)
                else:
                    yield {
                        "event": "progress",
                        "data": f"Skipped failed page: {page_url}",
                    }

            if not results:
                yield {
                    "event": "error",
                    "data": "I could not scrape any pages from this website.",
                }
                return

            combined_text = _build_combined_content(results, url)

            yield {
                "event": "progress",
                "data": f"Finished scraping {len(results)}/{total} pages. Generating answer...",
            }

            for token in stream_answer(
                question=question,
                combined_content=combined_text,
            ):
                yield {
                    "event": "answer",
                    "data": token,
                }

            yield {
                "event": "progress",
                "data": "Done.",
            }
