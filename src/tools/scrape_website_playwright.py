from bs4 import BeautifulSoup
from playwright.async_api import async_playwright


async def scrape_website_playwright(url: str) -> dict:
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)

        page = await browser.new_page(
            user_agent="Mozilla/5.0 (compatible; WebsiteScraperAgent/0.1)"
        )

        await page.goto(url, wait_until="networkidle", timeout=30000)

        html = await page.content()
        title = await page.title()

        await browser.close()

    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)

    return {
        "url": url,
        "title": title,
        "text": text,
        "html": html,
    }
