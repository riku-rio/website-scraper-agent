from mcp.server.fastmcp import FastMCP

from src.tools.fetch_website_pages import fetch_website_pages
from src.tools.scrape_website_bs4 import scrape_website_bs4
from src.tools.scrape_website_playwright import scrape_website_playwright


mcp = FastMCP("website-scraper-agent")


@mcp.tool()
def fetch_pages(url: str) -> list[str]:
    """Fetch internal pages from a website homepage."""
    return fetch_website_pages(url)


@mcp.tool()
def scrape_bs4(url: str) -> dict:
    """Scrape a webpage using requests and BeautifulSoup."""
    return scrape_website_bs4(url)


@mcp.tool()
async def scrape_playwright(url: str) -> dict:
    """Scrape a webpage using Playwright Chromium."""
    return await scrape_website_playwright(url)


if __name__ == "__main__":
    mcp.run()