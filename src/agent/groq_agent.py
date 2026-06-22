import json
import logging
import os
import re

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)

ALLOWED_STRATEGIES = {
    "page_only",
    "page_plus_related",
    "site_summary",
}


def get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise ValueError("GROQ_API_KEY is missing from .env")

    return Groq(api_key=api_key)


def choose_strategy(url: str, question: str) -> str:
    logger.info("[ROUTER] Choosing strategy: url=%s question=%s", url, question)

    try:
        client = get_groq_client()

        # Important:
        # Use a small non-reasoning model for routing.
        # Keep GROQ_MODEL for final answer generation.
        model = os.getenv("GROQ_ROUTER_MODEL", "llama-3.1-8b-instant")

        system_prompt = """
You are a routing component for a website scraping agent.

Choose the best scraping strategy for the user's URL and question.

Strategies:

page_only:
Use this when the user asks about one specific page, video, article, product, document, post, GitHub repo, or any deep URL.
Only scrape the exact requested URL.

page_plus_related:
Use this when the user asks about a specific page, but nearby or related pages may help answer the question.
Scrape the requested URL plus a small number of related pages.

site_summary:
Use this when the user asks about the whole website, company, service, or site.
Scrape multiple pages from the site.

Rules:
- If the URL is a deep content URL and the question is specific, choose page_only.
- If the question says "this video", "this article", "this page", "this product", or "this post", choose page_only.
- If the question asks to summarize or analyze the whole website or company, choose site_summary.
- If unsure, choose page_plus_related.

Return only valid JSON in this exact shape:
{"strategy":"page_only"}

Allowed strategy values:
- page_only
- page_plus_related
- site_summary
"""

        user_prompt = f"""
User URL:
{url}

User question:
{question}
"""

        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
            temperature=0.0,
            max_completion_tokens=128,
        )

        raw = response.choices[0].message.content or ""
        finish_reason = response.choices[0].finish_reason

        logger.info("[ROUTER] Raw response: %r", raw)
        logger.info("[ROUTER] Finish reason: %s", finish_reason)

        strategy = ""

        try:
            data = json.loads(raw)
            strategy = str(data.get("strategy", "")).strip().lower()
        except json.JSONDecodeError:
            raw_lower = raw.strip().lower()

            if raw_lower in ALLOWED_STRATEGIES:
                strategy = raw_lower
            else:
                for keyword in ALLOWED_STRATEGIES:
                    if re.search(re.escape(keyword), raw_lower):
                        strategy = keyword
                        break

        if strategy in ALLOWED_STRATEGIES:
            return strategy

        logger.warning(
            "[ROUTER] Invalid strategy (raw=%r), falling back to page_plus_related",
            raw,
        )
        return "page_plus_related"

    except Exception as e:
        logger.error(
            "[ROUTER] Failed: %s, falling back to page_plus_related",
            e,
        )
        return "page_plus_related"


def generate_answer(question: str, combined_content: str) -> str:
    client = get_groq_client()

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    system_prompt = """
You are a website scraper agent.

You answer user questions using the scraped website content provided below.

The first section, "PRIMARY REQUESTED PAGE", is the main page the user is asking about.
Sections labeled "RELATED PAGE 1", "RELATED PAGE 2", etc. are supporting context.

Base your answer primarily on the PRIMARY REQUESTED PAGE.
Use related pages only as supplementary context.
If the PRIMARY REQUESTED PAGE does not contain enough information to answer the question,
say that clearly. Do not fabricate answers from unrelated sections.

Keep the answer clear and useful.
"""

    user_prompt = f"""
User question:
{question}

Scraped content:
{combined_content[:12000]}
"""

    logger.info(
        "[LLM] Generating answer: model=%s input_length=%d",
        model,
        len(combined_content),
    )

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        temperature=0.2,
    )

    content = response.choices[0].message.content or ""
    logger.info("[LLM] Answer generated: %d chars", len(content))
    return content


def stream_answer(question: str, combined_content: str):
    client = get_groq_client()

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    system_prompt = """
You are a website scraper agent.

You answer user questions using the scraped website content provided below.

The first section, "PRIMARY REQUESTED PAGE", is the main page the user is asking about.
Sections labeled "RELATED PAGE 1", "RELATED PAGE 2", etc. are supporting context.

Base your answer primarily on the PRIMARY REQUESTED PAGE.
Use related pages only as supplementary context.
If the PRIMARY REQUESTED PAGE does not contain enough information to answer the question,
say that clearly. Do not fabricate answers from unrelated sections.

Keep the answer clear and useful. Use Markdown formatting when helpful.
"""

    user_prompt = f"""
User question:
{question}

Scraped content:
{combined_content[:12000]}
"""

    logger.info(
        "[LLM] Streaming answer: model=%s input_length=%d",
        model,
        len(combined_content),
    )

    stream = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        temperature=0.2,
        stream=True,
    )

    char_count = 0

    for chunk in stream:
        delta = chunk.choices[0].delta.content

        if delta:
            char_count += len(delta)
            yield delta

    logger.info("[LLM] Streaming finished: %d chars total", char_count)
