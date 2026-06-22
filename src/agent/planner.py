import ast
import json
import logging
import os
import re

from groq import Groq

from src.agent.groq_agent import get_groq_client

logger = logging.getLogger(__name__)

MAX_PLANNER_STEPS = 10

STRATEGY_LIMITS = {
    "page_only": 1,
    "page_plus_related": 3,
    "site_summary": 5,
}

ALLOWED_PLANNER_ACTIONS = {
    "fetch_pages",
    "scrape_bs4",
    "scrape_playwright",
    "finish",
}

_WEAK_SIGNALS = [
    "enable javascript",
    "please enable javascript",
    "checking your browser",
    "captcha",
    "access denied",
    "sign in to continue",
    "javascript is required",
    "enable javascript to view",
    "not accessible without javascript",
    "your browser does not support javascript",
]


def extract_tool_text(tool_result) -> str:
    if not tool_result.content:
        return ""
    first_content = tool_result.content[0]
    if hasattr(first_content, "text"):
        return first_content.text
    return str(first_content)


def _summarize(value, max_len=80) -> str:
    text = str(value)
    if len(text) > max_len:
        return text[:max_len] + "..."
    return text


async def call_mcp_tool(session, tool_name: str, arguments: dict):
    logger.info("[MCP CALL] tool=%s args=%s", tool_name, arguments)
    try:
        result = await session.call_tool(tool_name, arguments)
    except Exception as e:
        logger.error("[MCP ERR]  tool=%s - %s: %s", tool_name, type(e).__name__, e)
        raise
    if getattr(result, "structured_content", None) is not None:
        return result.structured_content["result"]
    if getattr(result, "structuredContent", None) is not None:
        return result.structuredContent["result"]
    text = extract_tool_text(result)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        parsed = ast.literal_eval(text)
    if isinstance(parsed, dict) and "result" in parsed:
        logger.info("[MCP OK]   tool=%s result=%s", tool_name, _summarize(parsed["result"]))
        return parsed["result"]
    logger.info("[MCP OK]   tool=%s result=%s", tool_name, _summarize(parsed))
    return parsed


def build_pages_to_scrape(user_url: str, discovered_pages: list[str], limit: int = 5) -> list[str]:
    pages = [user_url]
    for page in discovered_pages:
        if page not in pages:
            pages.append(page)
    return pages[:limit]


def basic_quality_review(result: dict) -> dict:
    text = result.get("text", "")
    title = result.get("title", "")

    text_lower = text.lower().strip()
    title_lower = title.lower().strip()

    if not text or len(text) < 10:
        return {
            "useful": False,
            "reason": "Empty or near-empty content.",
            "needs_ai_review": False,
            "should_try_playwright": True,
        }

    for signal in _WEAK_SIGNALS:
        if signal in text_lower:
            return {
                "useful": False,
                "reason": f"Content contains: '{signal}'",
                "needs_ai_review": False,
                "should_try_playwright": True,
            }

    for signal in _WEAK_SIGNALS:
        if signal in title_lower:
            return {
                "useful": False,
                "reason": f"Title contains: '{signal}'",
                "needs_ai_review": False,
                "should_try_playwright": True,
            }

    if len(text) < 250:
        return {
            "useful": False,
            "reason": f"Content too short ({len(text)} chars).",
            "needs_ai_review": False,
            "should_try_playwright": True,
        }

    if len(text) < 300:
        return {
            "useful": False,
            "reason": f"Content may be too short ({len(text)} chars).",
            "needs_ai_review": True,
            "should_try_playwright": False,
        }

    lines = [l.strip() for l in text.split("\n") if l.strip()]
    unique_ratio = len(set(lines)) / len(lines) if lines else 1.0

    if len(lines) < 15 and unique_ratio < 0.5:
        return {
            "useful": False,
            "reason": f"Mostly navigation/boilerplate ({len(lines)} lines, {unique_ratio:.0%} unique).",
            "needs_ai_review": True,
            "should_try_playwright": True,
        }

    nav_keywords = ["home", "about", "contact", "privacy", "terms", "cookie", "sign in", "register"]
    nav_count = sum(1 for kw in nav_keywords if kw in text_lower)

    if len(text) >= 500 and nav_count <= 3:
        return {
            "useful": True,
            "reason": f"Substantive content ({len(text)} chars).",
            "needs_ai_review": False,
            "should_try_playwright": False,
        }

    return {
        "useful": True,
        "reason": f"Content length {len(text)} chars, nav keywords {nav_count}.",
        "needs_ai_review": True,
        "should_try_playwright": False,
    }


def review_scrape_quality(question: str, url: str, result: dict, tool: str) -> dict:
    client = get_groq_client()
    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    text = result.get("text", "")
    title = result.get("title", "")
    text_preview = text[:1500]

    system_prompt = """You are reviewing scraped webpage content for usefulness.

Decide if the scraped content is useful for answering the user question.

Return JSON only:
{
  "useful": true | false,
  "reason": "short reason",
  "should_try_playwright": true | false
}

Rules:
- If content is empty, too short, mostly navigation, cookie text, login wall, captcha, access denied, or says JavaScript is required, useful=false.
- If content is relevant enough to help answer the user question, useful=true.
- If the current tool is scrape_bs4 and content is weak because rendering may be needed, should_try_playwright=true.
- Otherwise should_try_playwright=false."""

    user_prompt = f"""User question:
{question}

Scraped URL:
{url}

Scraping tool:
{tool}

Title:
{title}

Text preview:
{text_preview}"""

    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt.strip()},
            {"role": "user", "content": user_prompt.strip()},
        ],
        temperature=0.0,
        max_completion_tokens=256,
    )

    raw = response.choices[0].message.content or ""

    try:
        parsed = json.loads(raw)
        return {
            "useful": bool(parsed.get("useful", False)),
            "reason": str(parsed.get("reason", "")),
            "should_try_playwright": bool(parsed.get("should_try_playwright", False)),
        }
    except (json.JSONDecodeError, ValueError):
        logger.warning("[QUALITY AI] Failed to parse AI review: %r", raw)
        return {
            "useful": bool(text and len(text) > 300),
            "reason": "AI review parse failed, used fallback.",
            "should_try_playwright": False,
        }


def _build_planner_user_prompt(state: dict) -> str:
    parts = [
        f"User URL: {state['user_url']}",
        f"User question: {state['question']}",
        f"Selected strategy: {state['strategy']}",
        f"Allowed page limit: {state['limit']}",
        f"Step: {state['step']}/{MAX_PLANNER_STEPS}",
    ]

    discovered = state.get("discovered_pages", [])
    if discovered:
        parts.append(f"Discovered pages ({len(discovered)}): {discovered}")
    else:
        parts.append("Discovered pages: None (not fetched yet)")

    allowed = state.get("allowed_pages", [state["user_url"]])
    parts.append(f"Allowed pages: {allowed}")

    attempts = state.get("scraped_attempts", [])
    if attempts:
        parts.append(f"\nScraped results ({len(attempts)} attempts):")
        for i, a in enumerate(attempts, 1):
            result = a.get("result", {})
            text = result.get("text", "")
            title = result.get("title", "")
            quality = a.get("quality", {})
            useful = quality.get("useful")
            reason = quality.get("reason", "")
            err = a.get("error")
            err_suffix = f" ERROR: {err}" if err else ""
            parts.append(
                f"  [{i}] URL={a['url']} tool={a['tool']} "
                f"title='{title[:80]}' "
                f"len={len(text)} "
                f"useful={useful} "
                f"reason='{reason}'{err_suffix}"
            )
            if text and not err:
                parts.append(f"  Preview: {text[:1000]}")
    else:
        parts.append("\nScraped results: None yet")

    errors = state.get("errors", [])
    if errors:
        parts.append(f"\nErrors ({len(errors)}):")
        for e in errors:
            parts.append(f"  {e}")

    attempted_tools = state.get("attempted_tools", {})
    if attempted_tools:
        parts.append("\nAttempted tools per URL:")
        for u, tools in attempted_tools.items():
            parts.append(f"  {u}: {tools}")

    return "\n".join(parts)


def _call_llm_json(system_prompt: str, user_prompt: str) -> dict:
    try:
        client = get_groq_client()
    except Exception as e:
        logger.error("[PLANNER LLM] Client error: %s", e)
        return {}

    model = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt.strip()},
                {"role": "user", "content": user_prompt.strip()},
            ],
            temperature=0.0,
            max_completion_tokens=512,
        )
    except Exception as e:
        logger.error("[PLANNER LLM] API call failed: %s", e)
        return {}

    raw = response.choices[0].message.content or ""
    finish_reason = response.choices[0].finish_reason
    logger.info("[PLANNER LLM] Raw: %r finish=%s", raw[:200], finish_reason)

    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass

    match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1).strip())
            if isinstance(data, dict):
                return data
        except json.JSONDecodeError:
            pass

    return {}


PLANNER_SYSTEM_PROMPT = """You are a scraping planner for a website scraping agent.

You do not answer the user directly.
You only decide the next scraping action.

Available actions:
- fetch_pages
- scrape_bs4
- scrape_playwright
- finish

Definitions:
fetch_pages:
Discover internal pages from the user's website.

scrape_bs4:
Use a fast static HTML scraper. Best for simple/static pages.

scrape_playwright:
Use a browser-rendered scraper. Best for JavaScript-heavy pages, apps, pages where BS4 returned weak content, or pages requiring rendered DOM.

finish:
Use this when enough useful content has been collected to answer the user.

Rules:
- The exact user URL must be scraped first.
- If strategy is page_only, never call fetch_pages.
- If strategy is page_only, only scrape the exact user URL.
- If strategy is page_plus_related, scrape the user URL plus up to 2 related pages.
- If strategy is site_summary, scrape the user URL plus up to 4 related pages.
- Use scrape_bs4 for likely static pages.
- Use scrape_playwright for likely JavaScript-heavy pages.
- If BS4 returned only navigation, cookie text, empty content, login wall, captcha, access denied, or JavaScript-required text, choose scrape_playwright for the same URL.
- If enough useful content has been collected, choose finish.
- Never select a URL outside the allowed pages.
- Return JSON only.

Return exactly this shape:
{
  "action": "fetch_pages" | "scrape_bs4" | "scrape_playwright" | "finish",
  "url": "URL for scrape actions, otherwise empty string",
  "reason": "short reason"
}"""


def choose_next_planner_action(state: dict) -> dict:
    try:
        user_prompt = _build_planner_user_prompt(state)
        data = _call_llm_json(PLANNER_SYSTEM_PROMPT, user_prompt)
    except Exception as e:
        logger.error("[PLANNER] choose_next_planner_action failed: %s", e)
        return _safe_fallback_action(state)

    if not data:
        logger.warning("[PLANNER] Empty or invalid LLM response, using safe fallback")
        return _safe_fallback_action(state)

    action = str(data.get("action", "")).strip().lower()
    url = str(data.get("url", "")).strip()
    reason = str(data.get("reason", "")).strip()

    return {
        "action": action if action in ALLOWED_PLANNER_ACTIONS else "",
        "url": url,
        "reason": reason,
    }


def _find_untried_url(state: dict, tool: str):
    allowed = state.get("allowed_pages", [state["user_url"]])
    for page in allowed:
        page_tools = state.get("attempted_tools", {}).get(page, [])
        if tool not in page_tools:
            return page
    return None


def _override_scrape_user_url(state: dict) -> dict:
    user_url = state["user_url"]
    tools = state.get("attempted_tools", {}).get(user_url, [])
    if "scrape_bs4" not in tools:
        return {"action": "scrape_bs4", "url": user_url, "reason": "Must scrape exact URL first."}
    if "scrape_playwright" not in tools:
        return {"action": "scrape_playwright", "url": user_url, "reason": "Trying Playwright on user URL."}
    return {"action": "finish", "url": "", "reason": "All tools tried on user URL."}


def _safe_fallback_action(state: dict) -> dict:
    user_url = state["user_url"]
    allowed_pages = state.get("allowed_pages", [user_url])
    attempted_tools = state.get("attempted_tools", {})

    user_tools = attempted_tools.get(user_url, [])
    if "scrape_bs4" not in user_tools:
        return {"action": "scrape_bs4", "url": user_url, "reason": "Safe fallback: scrape user URL with BS4."}

    if "scrape_bs4" in user_tools and "scrape_playwright" not in user_tools:
        for a in state.get("scraped_attempts", []):
            if a["url"] == user_url and a["tool"] == "scrape_bs4":
                q = a.get("quality", {})
                if not q.get("useful", True) and q.get("should_try_playwright", False):
                    return {"action": "scrape_playwright", "url": user_url, "reason": "Safe fallback: BS4 weak, trying Playwright."}
                break

    for a in state.get("scraped_attempts", []):
        if a.get("quality", {}).get("useful"):
            return {"action": "finish", "url": "", "reason": "Safe fallback: useful content exists."}

    for page in allowed_pages:
        page_tools = attempted_tools.get(page, [])
        if "scrape_bs4" not in page_tools:
            return {"action": "scrape_bs4", "url": page, "reason": f"Safe fallback: scrape {page}."}

    for page in allowed_pages:
        page_tools = attempted_tools.get(page, [])
        if "scrape_playwright" not in page_tools:
            return {"action": "scrape_playwright", "url": page, "reason": f"Safe fallback: try Playwright on {page}."}

    return {"action": "finish", "url": "", "reason": "Safe fallback: all options exhausted."}


def _validate_planner_action(action: dict, state: dict) -> dict:
    raw_action = action.get("action", "")
    url = action.get("url", "")
    reason = action.get("reason", "")

    if raw_action not in ALLOWED_PLANNER_ACTIONS:
        logger.warning("[PLANNER] Invalid action: %r, safe fallback", raw_action)
        return _safe_fallback_action(state)

    strategy = state["strategy"]
    user_url = state["user_url"]
    allowed_pages = state.get("allowed_pages", [user_url])

    if raw_action == "fetch_pages" and strategy == "page_only":
        logger.warning("[PLANNER] Rejecting fetch_pages for page_only")
        return _override_scrape_user_url(state)

    if raw_action == "fetch_pages" and state.get("discovered_pages"):
        logger.warning("[PLANNER] fetch_pages already completed, redirecting")
        return _override_scrape_user_url(state)

    if raw_action in ("scrape_bs4", "scrape_playwright") and not url:
        logger.warning("[PLANNER] No URL specified for %s", raw_action)
        user_tools = state.get("attempted_tools", {}).get(user_url, [])
        if raw_action not in user_tools:
            return {"action": raw_action, "url": user_url, "reason": "No URL specified; using user URL."}
        alt_url = _find_untried_url(state, raw_action)
        if alt_url:
            return {"action": raw_action, "url": alt_url, "reason": "No URL specified; trying other page."}
        return _safe_fallback_action(state)

    if raw_action in ("scrape_bs4", "scrape_playwright") and url and url != user_url:
        user_tools = state.get("attempted_tools", {}).get(user_url, [])
        if not user_tools:
            logger.warning("[PLANNER] Must scrape user URL first, not %s", url)
            return {"action": raw_action, "url": user_url, "reason": "Must scrape exact URL first."}

    if raw_action in ("scrape_bs4", "scrape_playwright") and url and url not in allowed_pages:
        logger.warning("[PLANNER] URL %s not in allowed_pages", url)
        alt_url = _find_untried_url(state, raw_action)
        if alt_url:
            return {"action": raw_action, "url": alt_url, "reason": f"URL not allowed; trying {alt_url}."}
        return _safe_fallback_action(state)

    if raw_action in ("scrape_bs4", "scrape_playwright") and url:
        attempted = state.get("attempted_tools", {}).get(url, [])
        if raw_action in attempted:
            logger.warning("[PLANNER] Duplicate %s on %s", raw_action, url)
            alt_tool = "scrape_playwright" if raw_action == "scrape_bs4" else "scrape_bs4"
            if alt_tool not in attempted:
                return {"action": alt_tool, "url": url, "reason": f"Already tried {raw_action}; trying {alt_tool}."}
            alt_url = _find_untried_url(state, raw_action)
            if alt_url:
                return {"action": raw_action, "url": alt_url, "reason": f"Duplicate tool; trying {alt_url}."}
            return _safe_fallback_action(state)

    if raw_action == "finish":
        for page in allowed_pages:
            page_tools = state.get("attempted_tools", {}).get(page, [])
            if "scrape_bs4" in page_tools and "scrape_playwright" not in page_tools:
                for a in state.get("scraped_attempts", []):
                    if a["url"] == page and a["tool"] == "scrape_bs4":
                        q = a.get("quality", {})
                        if not q.get("useful", True) and q.get("should_try_playwright", False):
                            logger.warning("[PLANNER] Rejecting finish: weak BS4 on %s", page)
                            return {"action": "scrape_playwright", "url": page, "reason": f"BS4 weak on {page}; trying Playwright."}

    return action


def select_reviewed_results(state: dict) -> list[dict]:
    attempts = state.get("scraped_attempts", [])
    user_url = state["user_url"]

    by_url = {}
    for a in attempts:
        url = a["url"]
        if url not in by_url:
            by_url[url] = []
        by_url[url].append(a)

    def pick_best(url_attempts):
        useful = [a for a in url_attempts if a.get("quality", {}).get("useful")]
        weak = [a for a in url_attempts if not a.get("quality", {}).get("useful")]

        if useful:
            bs4_useful = [a for a in useful if a["tool"] == "scrape_bs4"]
            if bs4_useful:
                return bs4_useful[0]
            return useful[0]

        if weak:
            no_error = [a for a in weak if not a.get("error")]
            if no_error:
                return no_error[0]
            return weak[0]

        return None

    best_per_url = {}
    for url, url_attempts in by_url.items():
        best = pick_best(url_attempts)
        if best:
            best_per_url[url] = best

    results = []

    if user_url in best_per_url:
        a = best_per_url[user_url]
        q = a.get("quality", {})
        r = a.get("result", {})
        results.append({
            "url": user_url,
            "title": r.get("title", ""),
            "text": r.get("text", ""),
            "tool": a.get("tool", ""),
            "quality_reason": q.get("reason", ""),
            "useful": q.get("useful", False),
        })
        del best_per_url[user_url]

    related = []
    for url, a in best_per_url.items():
        q = a.get("quality", {})
        r = a.get("result", {})
        related.append({
            "url": url,
            "title": r.get("title", ""),
            "text": r.get("text", ""),
            "tool": a.get("tool", ""),
            "quality_reason": q.get("reason", ""),
            "useful": q.get("useful", False),
        })

    related.sort(key=lambda x: (1 if x["useful"] else 0, x["url"]), reverse=True)
    results.extend(related)

    return results


async def planner_loop(question: str, url: str, strategy: str, session):
    limit = STRATEGY_LIMITS.get(strategy, 5)

    state = {
        "user_url": url,
        "question": question,
        "strategy": strategy,
        "limit": limit,
        "discovered_pages": [],
        "allowed_pages": [url],
        "scraped_attempts": [],
        "errors": [],
        "attempted_tools": {},
        "step": 0,
    }

    logger.info("[PLANNER] Starting: strategy=%s url=%s limit=%d", strategy, url, limit)

    for step in range(1, MAX_PLANNER_STEPS + 1):
        state["step"] = step

        action = choose_next_planner_action(state)
        action = _validate_planner_action(action, state)

        raw_action = action.get("action", "")
        action_url = action.get("url", "")
        reason = action.get("reason", "")

        logger.info("[PLANNER] Step %d action=%s url=%s reason=%s", step, raw_action, action_url, reason)

        yield {
            "event": "progress",
            "data": f"Planner step {step}: {raw_action}\nReason: {reason}",
        }

        if raw_action == "finish":
            logger.info("[PLANNER] Finished at step %d", step)
            break

        if raw_action == "fetch_pages":
            try:
                yield {"event": "progress", "data": "Fetching related website pages..."}
                discovered = await call_mcp_tool(session, "fetch_pages", {"url": url})
                if not discovered:
                    discovered = [url]
                state["discovered_pages"] = discovered
                state["allowed_pages"] = build_pages_to_scrape(url, discovered, limit=limit)
                logger.info("[PLANNER] fetch_pages: discovered %d pages", len(discovered))
                yield {
                    "event": "progress",
                    "data": f"Discovered {len(discovered)} pages. Limit: {limit}.",
                }
            except Exception as e:
                logger.error("[PLANNER] fetch_pages failed: %s", e)
                state["errors"].append(f"fetch_pages: {e}")
                state["discovered_pages"] = [url]
                state["allowed_pages"] = build_pages_to_scrape(url, [url], limit=limit)
                yield {
                    "event": "progress",
                    "data": "Page discovery failed. Scraping provided URL only.",
                }
            continue

        if raw_action in ("scrape_bs4", "scrape_playwright"):
            target_url = action_url
            tool = raw_action

            if target_url not in state["attempted_tools"]:
                state["attempted_tools"][target_url] = []
            state["attempted_tools"][target_url].append(tool)

            yield {
                "event": "progress",
                "data": f"Scraping with {tool}:\n{target_url}",
            }

            try:
                result = await call_mcp_tool(session, tool, {"url": target_url})

                quality = basic_quality_review(result)

                if quality["needs_ai_review"]:
                    yield {"event": "progress", "data": "Reviewing scrape quality with AI..."}
                    quality = review_scrape_quality(question, target_url, result, tool)

                state["scraped_attempts"].append({
                    "url": target_url,
                    "tool": tool,
                    "result": result,
                    "quality": quality,
                    "error": None,
                })

                if quality["useful"]:
                    yield {
                        "event": "progress",
                        "data": f"Quality: useful - {quality['reason']}",
                    }
                else:
                    yield {
                        "event": "progress",
                        "data": f"Quality: weak - {quality['reason']}",
                    }
                    if quality["should_try_playwright"] and tool == "scrape_bs4":
                        yield {
                            "event": "progress",
                            "data": "Will try Playwright for better results.",
                        }

                logger.info(
                    "[PLANNER] Quality url=%s tool=%s useful=%s should_try_playwright=%s reason=%s",
                    target_url, tool, quality["useful"], quality["should_try_playwright"], quality["reason"],
                )

            except Exception as e:
                logger.error("[PLANNER] %s failed for %s: %s", tool, target_url, e)
                state["errors"].append(f"{tool} {target_url}: {type(e).__name__}: {e}")

                quality = {
                    "useful": False,
                    "reason": f"Exception: {type(e).__name__}",
                    "needs_ai_review": False,
                    "should_try_playwright": True,
                }

                state["scraped_attempts"].append({
                    "url": target_url,
                    "tool": tool,
                    "result": {"url": target_url, "title": "", "text": ""},
                    "quality": quality,
                    "error": str(e),
                })

                yield {
                    "event": "progress",
                    "data": f"{tool} failed: {type(e).__name__}",
                }

                if tool == "scrape_bs4":
                    yield {
                        "event": "progress",
                        "data": "Trying Playwright as fallback...",
                    }

                    pw_tool = "scrape_playwright"
                    if target_url not in state["attempted_tools"]:
                        state["attempted_tools"][target_url] = []
                    state["attempted_tools"][target_url].append(pw_tool)

                    try:
                        pw_result = await call_mcp_tool(session, pw_tool, {"url": target_url})
                        pw_quality = basic_quality_review(pw_result)
                        if pw_quality["needs_ai_review"]:
                            pw_quality = review_scrape_quality(question, target_url, pw_result, pw_tool)

                        state["scraped_attempts"].append({
                            "url": target_url,
                            "tool": pw_tool,
                            "result": pw_result,
                            "quality": pw_quality,
                            "error": None,
                        })

                        if pw_quality["useful"]:
                            yield {"event": "progress", "data": f"Quality: useful - {pw_quality['reason']}"}
                        else:
                            yield {"event": "progress", "data": f"Quality: weak - {pw_quality['reason']}"}
                    except Exception as pw_e:
                        logger.error("[PLANNER] Playwright also failed for %s: %s", target_url, pw_e)
                        state["errors"].append(f"scrape_playwright {target_url}: {type(pw_e).__name__}: {pw_e}")
                        state["scraped_attempts"].append({
                            "url": target_url,
                            "tool": pw_tool,
                            "result": {"url": target_url, "title": "", "text": ""},
                            "quality": {
                                "useful": False,
                                "reason": f"Exception: {type(pw_e).__name__}",
                                "needs_ai_review": False,
                                "should_try_playwright": False,
                            },
                            "error": str(pw_e),
                        })
                        yield {"event": "progress", "data": f"Playwright also failed: {type(pw_e).__name__}"}

            continue

    reviewed = select_reviewed_results(state)

    useful_count = sum(1 for r in reviewed if r.get("useful"))
    total_attempts = len(state["scraped_attempts"])

    logger.info(
        "[PLANNER] Finished: useful_pages=%d total_attempts=%d",
        useful_count, total_attempts,
    )

    yield {
        "event": "progress",
        "data": f"Planner finished. Generating answer... ({useful_count}/{len(reviewed)} useful pages)",
    }

    yield {
        "event": "planner_done",
        "data": reviewed,
    }


async def run_planner_scraping(question: str, url: str, strategy: str, session) -> list[dict]:
    reviewed = []
    async for event in planner_loop(question, url, strategy, session):
        if event.get("event") == "planner_done":
            reviewed = event.get("data", [])
            break
    return reviewed
