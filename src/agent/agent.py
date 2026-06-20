import ast
import json
import logging

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

            target_page = pages[0]

            try:
                result = await call_mcp_tool(
                    session,
                    "scrape_bs4",
                    {"url": target_page},
                )
                scraper_used = "bs4"
            except Exception:
                result = await call_mcp_tool(
                    session,
                    "scrape_playwright",
                    {"url": target_page},
                )
                scraper_used = "playwright"

            answer = generate_answer(
                question=question,
                page_url=result["url"],
                page_title=result["title"],
                page_text=result["text"],
            )

            logger.info("[AGENT] Finished: scraper=%s page=%s", scraper_used, result["url"])

            return f"""
            Scraper used: {scraper_used}
            Page: {result["url"]}

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

            target_page = pages[0]

            yield f"Scraping page: `{target_page}`\n\n"

            try:
                result = await call_mcp_tool(
                    session,
                    "scrape_bs4",
                    {"url": target_page},
                )
                scraper_used = "bs4"
            except Exception:
                result = await call_mcp_tool(
                    session,
                    "scrape_playwright",
                    {"url": target_page},
                )
                scraper_used = "playwright"

            yield f"**Scraper used:** `{scraper_used}`\n\n"
            yield f"**Page:** {result['url']}\n\n---\n\n"

            for token in stream_answer(
                question=question,
                page_url=result["url"],
                page_title=result["title"],
                page_text=result["text"],
            ):
                yield token
