import logging

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from src.agent.groq_agent import choose_strategy, generate_answer, stream_answer
from src.agent.planner import planner_loop, run_planner_scraping

logger = logging.getLogger(__name__)


def _build_combined_content(results: list[dict], user_url: str) -> str:
    parts = []
    related_idx = 0
    for r in results:
        url = r.get('url', '')
        if url == user_url:
            parts.append(
                f"PRIMARY REQUESTED PAGE:\n"
                f"URL: {url}\n"
                f"TITLE: {r.get('title', '')}\n"
                f"SCRAPER: {r.get('tool', '')}\n"
                f"QUALITY: {r.get('quality_reason', '')}\n"
                f"CONTENT:\n"
                f"{r.get('text', '')}"
            )
        else:
            related_idx += 1
            parts.append(
                f"RELATED PAGE {related_idx}:\n"
                f"URL: {url}\n"
                f"TITLE: {r.get('title', '')}\n"
                f"SCRAPER: {r.get('tool', '')}\n"
                f"QUALITY: {r.get('quality_reason', '')}\n"
                f"CONTENT:\n"
                f"{r.get('text', '')}"
            )
    return "\n\n---\n\n".join(parts)


async def run_agent(question: str, url: str) -> str:
    logger.info("[AGENT] Starting: question=%s url=%s", question, url)

    strategy = choose_strategy(url, question)
    logger.info("[AGENT] Selected scraping strategy: %s url=%s question=%s", strategy, url, question)

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "src.mcp_server.server"],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            reviewed_results = await run_planner_scraping(
                question=question,
                url=url,
                strategy=strategy,
                session=session,
            )

            if not reviewed_results:
                return "I could not scrape any pages from this website."

            combined_text = _build_combined_content(reviewed_results, url)

            answer = generate_answer(
                question=question,
                combined_content=combined_text,
            )

            logger.info(
                "[AGENT] Finished: %d pages scraped",
                len(reviewed_results),
            )

            page_list = "\n".join(
                f"- {r['url']} ({r['tool']})" for r in reviewed_results
            )

            return f"""
Pages scraped ({len(reviewed_results)}):
{page_list}

{answer}
            """


async def stream_agent(question: str, url: str):
    logger.info("[AGENT] Starting (stream): question=%s url=%s", question, url)

    strategy = choose_strategy(url, question)
    logger.info("[AGENT] Selected scraping strategy: %s url=%s question=%s", strategy, url, question)

    yield {
        "event": "progress",
        "data": f"Selected scraping strategy: {strategy}",
    }

    server_params = StdioServerParameters(
        command="uv",
        args=["run", "python", "-m", "src.mcp_server.server"],
    )

    async with stdio_client(server_params) as (read_stream, write_stream):
        async with ClientSession(read_stream, write_stream) as session:
            await session.initialize()

            reviewed_results = None

            async for event in planner_loop(question, url, strategy, session):
                if event.get("event") == "planner_done":
                    reviewed_results = event.get("data", [])
                    break
                yield event

            if not reviewed_results:
                yield {
                    "event": "error",
                    "data": "I could not scrape any pages from this website.",
                }
                return

            combined_text = _build_combined_content(reviewed_results, url)

            yield {
                "event": "progress",
                "data": f"Finished scraping {len(reviewed_results)} pages. Generating answer...",
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
