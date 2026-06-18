import ast
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def extract_tool_text(tool_result) -> str:
    if not tool_result.content:
        return ""

    first_content = tool_result.content[0]

    if hasattr(first_content, "text"):
        return first_content.text

    return str(first_content)


async def call_mcp_tool(session: ClientSession, tool_name: str, arguments: dict):
    result = await session.call_tool(tool_name, arguments)

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
        return parsed["result"]

    return parsed


async def run_agent(question: str, url: str) -> str:
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

            return f"""
Question:
{question}

Scraper used:
{scraper_used}

Page:
{result["url"]}

Title:
{result["title"]}

Content preview:
{result["text"][:1500]}
"""