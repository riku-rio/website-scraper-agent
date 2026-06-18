from src.tools.fetch_website_pages import fetch_website_pages
from src.tools.scrape_website_bs4 import scrape_website_bs4
from src.tools.scrape_website_playwright import scrape_website_playwright


async def run_agent(question: str, url: str) -> str:
    pages = fetch_website_pages(url)

    if not pages:
        return "I could not find any pages on this website."

    target_page = pages[0]

    try:
        result = scrape_website_bs4(target_page)
        scraper_used = "bs4"
    except Exception:
        result = await scrape_website_playwright(target_page)
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