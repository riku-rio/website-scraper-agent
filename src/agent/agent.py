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


def _build_combined_content(results: list[dict]) -> str:
    """Combine multiple scraped pages into a single formatted string."""
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(
            f"Page {i}\n"
            f"URL: {r.get('url', '')}\n"
            f"Title: {r.get('title', '')}\n\n"
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

            pages = await call_mcp_tool(
                session,
                "fetch_pages",
                {"url": url},
            )

            if not pages:
                return "I could not find any pages on this website."

            max_pages = _get_max_scrape_pages()
            pages_to_scrape = pages[:max_pages]

            results = []
            for page_url in pages_to_scrape:
                result = await _scrape_page_with_fallback(session, page_url)
                if result is not None:
                    results.append(result)

            if not results:
                return "I could not scrape any pages from this website."

            combined_text = _build_combined_content(results)

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

            yield "Fetching website pages...\n\n"

            pages = await call_mcp_tool(
                session,
                "fetch_pages",
                {"url": url},
            )

            if not pages:
                yield "I could not find any pages on this website."
                return

            max_pages = _get_max_scrape_pages()
            pages_to_scrape = pages[:max_pages]
            total = len(pages_to_scrape)

            yield f"Found {len(pages)} pages. Scraping up to {max_pages} pages...\n\n"

            results = []
            for i, page_url in enumerate(pages_to_scrape, 1):
                yield f"Scraping {i}/{total}: {page_url}\n\n"

                result = await _scrape_page_with_fallback(session, page_url)
                if result is not None:
                    results.append(result)
                else:
                    yield f"Skipped failed page: {page_url}\n\n"

            if not results:
                yield "I could not scrape any pages from this website."
                return

            combined_text = _build_combined_content(results)

            yield f"**Pages scraped:** {len(results)}/{total}\n\n"
            yield f"**Question:** {question}\n\n---\n\n"

            for token in stream_answer(
                question=question,
                combined_content=combined_text,
            ):
                yield token
