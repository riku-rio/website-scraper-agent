import logging
import os

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

logger = logging.getLogger(__name__)


def get_groq_client() -> Groq:
    api_key = os.getenv("GROQ_API_KEY")

    if not api_key:
        raise ValueError("GROQ_API_KEY is missing from .env")

    return Groq(api_key=api_key)


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

    logger.info("[LLM] Generating answer: model=%s input_length=%d", model, len(combined_content))

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        temperature=0.2,
    )

    content = response.choices[0].message.content
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

    logger.info("[LLM] Streaming answer: model=%s input_length=%d", model, len(combined_content))

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
