import requests
from bs4 import BeautifulSoup


def scrape_website_bs4(url: str) -> dict:
    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WebsiteScraperAgent/0.1)"
        },
    )
    response.raise_for_status()
    response.encoding = response.apparent_encoding

    html = response.text
    soup = BeautifulSoup(html, "html.parser")

    title = soup.title.get_text(strip=True) if soup.title else ""

    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n", strip=True)

    return {
        "url": url,
        "title": title,
        "text": text,
        "html": html,
    }