from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup


def normalize_url(url: str) -> str:
    parsed = urlparse(url)

    if not parsed.scheme:
        url = "https://" + url
        parsed = urlparse(url)

    return url.rstrip("/")


def is_same_domain(url: str, base_domain: str) -> bool:
    parsed = urlparse(url)
    return parsed.netloc == base_domain


def fetch_website_pages(url: str) -> list[str]:
    base_url = normalize_url(url)
    base_domain = urlparse(base_url).netloc

    response = requests.get(
        base_url,
        timeout=15,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; WebsiteScraperAgent/0.1)"
        },
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    pages = set()
    pages.add(base_url)

    for link in soup.find_all("a", href=True):
        href = link.get("href")

        if not href:
            continue

        absolute_url = urljoin(base_url + "/", href)
        parsed_url = urlparse(absolute_url)

        if parsed_url.scheme not in ["http", "https"]:
            continue

        clean_url = parsed_url._replace(fragment="", query="").geturl().rstrip("/")

        if is_same_domain(clean_url, base_domain):
            pages.add(clean_url)

    return sorted(pages)