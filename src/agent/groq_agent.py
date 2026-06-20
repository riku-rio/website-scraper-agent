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


def generate_answer(question: str, page_url: str, page_title: str, page_text: str) -> str:
    client = get_groq_client()

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    system_prompt = """
You are a website scraper agent.

You answer user questions using only the scraped website content provided to you.
If the answer is not available in the content, say that clearly.
Do not invent facts.
Keep the answer clear and useful.
"""

    user_prompt = f"""
User question:
{question}

Scraped page:
URL: {page_url}
Title: {page_title}

Content:
{page_text[:12000]}
"""

    logger.info("[LLM] Generating answer: model=%s page_text_length=%d", model, len(page_text))

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

def stream_answer(question: str, page_url: str, page_title: str, page_text: str):
    client = get_groq_client()

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    system_prompt = """
You are a website scraper agent.

You answer user questions using only the scraped website content provided to you.
If the answer is not available in the content, say that clearly.
Do not invent facts.
Keep the answer clear and useful.
Use Markdown formatting when helpful.
"""

    user_prompt = f"""
User question:
{question}

Scraped page:
URL: {page_url}
Title: {page_title}

Content:
{page_text[:12000]}
"""

    logger.info("[LLM] Streaming answer: model=%s page_text_length=%d", model, len(page_text))

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
