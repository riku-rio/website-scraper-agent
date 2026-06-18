# from src.tools.fetch_website_pages import fetch_website_pages


# def main():
#     pages = fetch_website_pages("https://books.toscrape.com")

#     for page in pages:
#         print(page)


# if __name__ == "__main__":
#     main()

# from src.tools.scrape_website_bs4 import scrape_website_bs4


# def main():
#     result = scrape_website_bs4(
#         "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
#     )

#     print("Title:", result["title"])
#     print("Text preview:")
#     print(result["text"][:1000])


# if __name__ == "__main__":
#     main()

# from src.tools.scrape_website_playwright import scrape_website_playwright


# def main():
#     result = scrape_website_playwright(
#         "https://books.toscrape.com/catalogue/a-light-in-the-attic_1000/index.html"
#     )

#     print("Title:", result["title"])
#     print("Text preview:")
#     print(result["text"][:1000])


# if __name__ == "__main__":
#     main()

import asyncio

from src.agent.agent import run_agent


async def main():
    answer = await run_agent(
        question="Summarize this website",
        url="https://books.toscrape.com",
    )

    print(answer)


if __name__ == "__main__":
    asyncio.run(main())