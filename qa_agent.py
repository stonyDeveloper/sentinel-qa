import argparse
import base64
import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from langchain_core.tools import tool
from openai import OpenAI
from playwright.sync_api import sync_playwright
from report import generate_report

load_dotenv(override=True)

CHROME_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
LAUNCH_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
]
STEALTH_JS = """
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
window.chrome = window.chrome || { runtime: {} };
Object.defineProperty(navigator, 'languages', { get: () => ['en-US', 'en'] });
Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
"""
CAPTCHA_SELECTOR = (
    "iframe[src*='turnstile'], iframe[src*='hcaptcha'], iframe[src*='recaptcha'], "
    "iframe[src*='challenges.cloudflare.com'], .cf-turnstile, .h-captcha, .g-recaptcha, "
    "[data-sitekey]"
)

_pw = sync_playwright().start()
browser = _pw.chromium.launch(headless=True, args=LAUNCH_ARGS)
context = browser.new_context(
    viewport={"width": 1280, "height": 800},
    user_agent=CHROME_UA,
    locale="en-US",
    timezone_id="UTC",
)
context.add_init_script(STEALTH_JS)
page = context.new_page()
page.set_default_timeout(8000)

SHOT_DATA: dict[str, bytes] = {}
SHOTS_STREAM = os.getenv("QA_SHOTS_STREAM") or ""

PACING_SECONDS = float(os.getenv("QA_PACING", "8"))
LOOP_CAP = 8
REPEAT_LIMIT = 3
SETTLE_MS = int(os.getenv("QA_SETTLE_MS", "1600"))
SETTLE_TOOLS = {"click", "click_selector", "click_href", "press", "type_text"}
REPEAT_GUARD_TOOLS = {"click", "click_selector", "click_href", "type_text", "press", "hover", "navigate"}
OAUTH_HOSTS = ("accounts.google.com", "appleid.apple.com", "login.live.com", "login.microsoftonline.com", "login.microsoft.com")
OAUTH_PREFIXES = (("github.com", "/login"), ("linkedin.com", "/oauth"))


def _shot_name(evidence_name: str) -> str:
    name = "".join("_" if ch in '\\/:*?"<>|' or ch == " " else ch for ch in evidence_name)
    return name + ".png"


def _append_shot(name: str, png: bytes) -> None:
    if not SHOTS_STREAM:
        return
    try:
        path = Path(SHOTS_STREAM)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"name": name, "png": base64.b64encode(png).decode("ascii")}) + "\n")
    except OSError:
        pass


def _session_path(url: str):
    host = (urlparse(url).hostname or "").lower().replace(":", "_")
    base = os.getenv("QA_SESSIONS_DIR", "sessions")
    if not host or not base:
        return None
    return Path(base) / (host + ".json")


def _load_session(url: str) -> None:
    p = _session_path(url)
    if p is None or not p.exists():
        return
    try:
        state = json.loads(p.read_text(encoding="utf-8"))
        if state.get("cookies"):
            context.add_cookies(state["cookies"])
        print(f"\n[loaded saved session: {p}]")
    except Exception as exc:
        print(f"[session load failed: {exc}]")


def _save_session(url: str) -> None:
    p = _session_path(url)
    if p is None:
        return
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        state = context.storage_state()
        p.write_text(json.dumps(state), encoding="utf-8")
        print(f"\n[saved session state: {p}]")
    except Exception as exc:
        print(f"[session save failed: {exc}]")


def _captcha_detect() -> str:
    try:
        found = page.eval_on_selector_all(
            CAPTCHA_SELECTOR,
            """els => els.slice(0, 4).map(e => {
                const src = (e.getAttribute('src') || '').toLowerCase();
                const cls = ((e.className || '') + ' ' + (e.getAttribute('data-sitekey') ? 'sitekey' : '')).toLowerCase();
                const combo = src + ' ' + cls;
                if (combo.includes('turnstile') || combo.includes('challenges.cloudflare')) return 'Turnstile';
                if (combo.includes('hcaptcha')) return 'hCaptcha';
                if (combo.includes('recaptcha')) return 'reCAPTCHA';
                return 'captcha-widget';
            })""",
        )
        uniq = sorted(set(found))
        return ", ".join(uniq) if uniq else ""
    except Exception:
        return ""


def _oauth_warning() -> str:
    try:
        url = page.url
    except Exception:
        return ""
    host = (urlparse(url).hostname or "").lower()
    if host in OAUTH_HOSTS:
        return host
    path = urlparse(url).path
    for h, prefix in OAUTH_PREFIXES:
        if host == h and path.startswith(prefix):
            return h
    return ""


@tool
def navigate(url: str) -> str:
    """Navigate the browser to a URL. Returns the page title so you know you got there. Resets to a single tab."""
    global page
    for extra in context.pages[1:]:
        extra.close()
    page = context.pages[0]
    page.bring_to_front()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(600)
    return f"Navigated. Page title: '{page.title()}'"


@tool
def read_page() -> str:
    """Read text currently visible in the browser viewport only, plus the current URL and title. Call this after every action to see the result."""
    try:
        base = page.evaluate("() => location.href + ' | title: ' + document.title")
        body = page.evaluate(
            """() => {
                const vh = window.innerHeight, vw = window.innerWidth;
                const out = [];
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node, total = 0;
                while ((node = walker.nextNode()) && total < 1500) {
                    const t = node.textContent.trim();
                    if (!t) continue;
                    const el = node.parentElement;
                    if (!el) continue;
                    const tag = el.tagName;
                    if (tag === 'SCRIPT' || tag === 'STYLE') continue;
                    const r = el.getBoundingClientRect();
                    if (r.bottom < 0 || r.top > vh || r.right < 0 || r.left > vw) continue;
                    const piece = t.slice(0, 300);
                    out.push(piece);
                    total += piece.length;
                }
const hints = [...document.querySelectorAll('a[aria-label],button[aria-label],[role="button"][aria-label],a[class*="social"] i, a i.fa, a.instagram, a.facebook, a.twitter, a.linkedin, a.youtube')]
                .filter(e => {
                    const r = e.getBoundingClientRect();
                    return r.bottom > 0 && r.top < vh && r.right > 0 && r.left < vw;
                })
                .filter(e => (e.closest('a')?.innerText || '').trim().length === 0)
                .map(e => {
                    const a = e.closest('a') || e;
                    const aria = a.getAttribute('aria-label');
                    if (aria) return '(' + a.tagName.toLowerCase() + ':' + aria + ')';
                    const cls = (a.className + ' ' + (e.className || '')).toLowerCase();
                    const names = ['facebook','twitter','linkedin','instagram','youtube','pinterest','github'];
                    const hit = names.find(n => cls.includes(n));
                    return hit ? '(' + a.tagName.toLowerCase() + ':' + hit + ')' : null;
                })
                .filter(Boolean);
                for (const h of hints.slice(0, 20)) {
                    if (total >= 1900) break;
                    out.push(h);
                    total += h.length;
                }
                return out.join(' | ').slice(0, 2000);
            }"""
        )
        cap = _captcha_detect()
        if cap:
            body += f"\n[automated-verification detected: {cap}]"
        return base + "\n" + body
    except Exception as exc:
        return f"ERROR reading page: {exc}"


@tool
def click(text: str) -> str:
    """Click an element by its visible text (e.g. 'Add', 'Submit', 'Delete'). Returns new page text."""
    page.get_by_text(text, exact=True).first.click()
    page.wait_for_timeout(300)
    return page.inner_text("body")[:3000]


@tool
def click_selector(selector: str) -> str:
    """Click the first element matching a CSS selector (e.g. '.toggle', '[data-testid="delete"]'). Use for elements without visible text."""
    page.locator(selector).first.click()
    page.wait_for_timeout(300)
    return page.inner_text("body")[:3000]


@tool
def hover(text: str) -> str:
    """Hover over an element by its visible text. Required before interacting with hover-only elements like delete buttons."""
    page.get_by_text(text, exact=True).first.hover()
    page.wait_for_timeout(300)
    return f"Hovered over '{text}'."


@tool
def get_links() -> str:
    """List clickable links currently visible as 'label -> url'. Works for links with text AND icon-only links (identified by aria-label, title, or an <img> alt). Call this before clicking so you use real labels and URLs instead of guessing."""
    try:
        links = page.eval_on_selector_all(
            "a[href]",
            """els => els.filter(e => {
                const r = e.getBoundingClientRect();
                return r.bottom > 0 && r.top < window.innerHeight && r.right > 0 && r.left < window.innerWidth;
            })
            .map(e => {
                let label = (e.innerText || '').trim().split('\\n')[0].slice(0, 60);
                if (!label) label = (e.getAttribute('aria-label') || '').trim();
                if (!label) label = (e.getAttribute('title') || '').trim();
                if (!label) {
                    const img = e.querySelector('img[alt]');
                    if (img) label = img.getAttribute('alt').trim();
                }
                if (!label) {
                    const names = ['facebook','twitter','linkedin','instagram','youtube',
                        'pinterest','tiktok','github','whatsapp','telegram','reddit','x ', 'follow'];
                    const classes = ((e.className || '') + ' ' + (e.querySelector('i,svg,span')?.className || '')).toLowerCase();
                    const hit = names.find(n => classes.includes(n));
                    if (hit) label = hit.trim() === 'x ' ? 'Twitter' : hit;
                }
                label = (label || '').trim();
                return label + ' -> ' + e.getAttribute('href');
            })
            .filter(l => l !== ' -> ' && !l.includes('self.__next_f'))""",
        )
        return "\n".join(links[:40]) or "No visible links."
    except Exception as exc:
        return f"ERROR listing links: {exc}"


@tool
def click_href(substring: str) -> str:
    """Click the first link whose URL contains the given substring (e.g. 'medium.com'). Use for following links instead of guessing visible text."""
    page.locator(f"a[href*='{substring}']").first.click()
    page.wait_for_timeout(300)
    return f"Clicked first link whose URL contains '{substring}'."


@tool
def scroll(direction: str) -> str:
    """Scroll the page. direction must be 'down' or 'up'. Returns scroll progress; call read_page to inspect the visible content."""
    page.mouse.wheel(0, 1200 if direction == "down" else -1200)
    page.wait_for_timeout(300)
    progress = page.evaluate("() => `${window.scrollY}/${document.body.scrollHeight}`")
    return f"Scrolled {direction}. Viewport offset {progress}. Call read_page to inspect visible content."


@tool
def scroll_to_text(text: str) -> str:
    """Scroll down the whole page (up to 40 viewports) until some visible text containing the given phrase is found. Use to jump straight to a far section like 'ARTICLES' or 'PROJECTS' instead of many single scrolls."""
    for _ in range(40):
        y = page.evaluate("() => window.scrollY")
        end = page.evaluate("() => document.body.scrollHeight - window.innerHeight")
        hits = page.evaluate(
            """(t) => [...document.querySelectorAll('h1,h2,h3,h4,h5,h6,p,a,span')]
                .filter(e => {
                    const r = e.getBoundingClientRect();
                    return r.bottom > 0 && r.top < window.innerHeight &&
                           (e.innerText || '').toLowerCase().includes(t);
                })
                .map(e => e.tagName + ':' + (e.innerText || '').trim().slice(0, 40))
                .slice(0, 3)""",
            text.lower(),
        )
        if hits:
            return f"Found '{text}' on screen: {hits[0]}. Viewport offset {y}."
        if y >= end:
            return f"Scrolled to bottom ({y}/{end}) without finding '{text}'."
        page.mouse.wheel(0, 1200)
        page.wait_for_timeout(200)
    return f"Scrolled 40 viewports without finding '{text}'."


@tool
def type_text(placeholder_or_label: str, value: str) -> str:
    """Type text into an input field. Use the field's placeholder text or aria-label to find it."""
    locator = page.get_by_placeholder(placeholder_or_label, exact=False)
    if locator.count() == 0:
        locator = page.get_by_label(placeholder_or_label, exact=False)
    target = locator.first
    how = "typed"
    try:
        target.click(timeout=4000)
        try:
            target.press_sequentially(value, delay=random.randint(28, 85))
        except Exception:
            target.fill(value)
            how = "filled"
    except Exception:
        try:
            target.fill(value, timeout=4000)
            how = "filled"
        except Exception:
            target.evaluate(
                """(el, v) => {
                    const proto = el.constructor.prototype;
                    const set = Object.getOwnPropertyDescriptor(proto, 'value');
                    if (set && set.set) set.set.call(el, v);
                    el.value = v;
                    el.dispatchEvent(new Event('input', { bubbles: true }));
                    el.dispatchEvent(new Event('change', { bubbles: true }));
                }""",
                value,
            )
            how = "js-set"
    return f"Typed '{value}' into field '{placeholder_or_label}' ({how})"


@tool
def press(key: str) -> str:
    """Press a keyboard key like 'Enter' or 'Escape'. Returns new page text."""
    page.keyboard.press(key)
    page.wait_for_timeout(300)
    return page.inner_text("body")[:3000]


def _sync_tabs() -> list[str]:
    global page
    for _ in range(10):
        if len(context.pages) > 1:
            break
        page.wait_for_timeout(100)
    if len(context.pages) <= 1:
        return []
    candidate = context.pages[-1]
    if page != candidate:
        page = candidate
        page.bring_to_front()
        page.wait_for_timeout(600)
        return [f"{page.title()} | {page.url}"]
    return []


@tool
def close_tab() -> str:
    """Close the current tab (e.g. a Medium article that opened a new tab) and return to the main page."""
    global page
    if len(context.pages) > 1:
        page.close()
        page = context.pages[0]
        page.bring_to_front()
        return f"Closed extra tab. Back on page: '{page.title()}'."
    return "Only one tab is open."


@tool
def screenshot(evidence_name: str) -> str:
    """Take a screenshot as evidence. Use a short descriptive name without spaces."""
    name = _shot_name(evidence_name)
    png = page.screenshot(full_page=True)
    SHOT_DATA[name] = png
    _append_shot(name, png)
    return f"Screenshot saved: {name}"


TOOLS = {
    "navigate": navigate,
    "read_page": read_page,
    "get_links": get_links,
    "click": click,
    "click_href": click_href,
    "click_selector": click_selector,
    "hover": hover,
    "scroll": scroll,
    "scroll_to_text": scroll_to_text,
    "type_text": type_text,
    "press": press,
    "close_tab": close_tab,
    "screenshot": screenshot,
}

TOOL_SCHEMAS = [
    {"type": "function", "function": {"name": "navigate", "description": navigate.description, "parameters": {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]}}},
    {"type": "function", "function": {"name": "read_page", "description": read_page.description, "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "get_links", "description": get_links.description, "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "click", "description": click.description, "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "click_href", "description": click_href.description, "parameters": {"type": "object", "properties": {"substring": {"type": "string"}}, "required": ["substring"]}}},
    {"type": "function", "function": {"name": "click_selector", "description": click_selector.description, "parameters": {"type": "object", "properties": {"selector": {"type": "string"}}, "required": ["selector"]}}},
    {"type": "function", "function": {"name": "hover", "description": hover.description, "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "scroll", "description": scroll.description, "parameters": {"type": "object", "properties": {"direction": {"type": "string"}}, "required": ["direction"]}}},
    {"type": "function", "function": {"name": "scroll_to_text", "description": scroll_to_text.description, "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}},
    {"type": "function", "function": {"name": "type_text", "description": type_text.description, "parameters": {"type": "object", "properties": {"placeholder_or_label": {"type": "string"}, "value": {"type": "string"}}, "required": ["placeholder_or_label", "value"]}}},
    {"type": "function", "function": {"name": "press", "description": press.description, "parameters": {"type": "object", "properties": {"key": {"type": "string"}}, "required": ["key"]}}},
    {"type": "function", "function": {"name": "close_tab", "description": close_tab.description, "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "screenshot", "description": screenshot.description, "parameters": {"type": "object", "properties": {"evidence_name": {"type": "string"}}, "required": ["evidence_name"]}}},
]

SYSTEM_PROMPT = """You are a QA test engineer working in a real browser through tools.

Your workflow for every test:
1. Investigate: navigate to the app and read the page.
2. Exercise: try the core flows. Add, edit, delete, submit, navigate — behave like a real user.
3. Verify: after each action, read the page and check the result is correct.
4. Report: when you have enough evidence, write a final report. Structure it as:
   - 'Passed checks:' — ONLY the acceptance criteria from your task that you confirmed WORK.
   - 'Bugs found:' — ONLY the acceptance criteria from your task that FAILED, with exact steps to reproduce.
   NEVER list routine actions you performed (navigate, scroll, screenshot, read_page) as 'passed checks' or 'bugs'. Those are not acceptance criteria. Your final report must be strictly about the user's stated task.

Rules:
- NEVER narrate or describe your thinking. Your reply must ALWAYS be either a tool call,
  or your final report when you are done. If unsure what to do next, pick the single most
  sensible action and take it.
- Never call the same tool more than a few times in a row. Alternate actions and read the
  page between them instead of repeating scroll over and over.
- You can only see what the read_page tool returns: text currently VISIBLE in the viewport.
  The whole page is not shown to you at once, one viewport at a time.
- Before clicking a link, call get_links() to see the real link text and URLs on screen.
  To follow a link, use click_href() with a piece of its URL (e.g. 'medium.com') — never
  guess text that isn't in front of you. If your click times out, the text is NOT on the
  page; scroll or get_links again instead of retrying the same thing.
- Elements may be below the fold: scroll('down') moves one viewport and read_page shows the new
  content. Scroll repeatedly to travel down the page; watch the offset hint to know when the
  bottom is reached. To reach a far section fast, use scroll_to_text('ARTICLES') in one call.
- Stay strictly on the assigned task. Do not wander into unrelated links (for example project
  demo 'Live' links) unless the task mentions them.
- If a modal/overlay opens (dark backdrop covering the page), close it with press('Escape')
  or click its close button before continuing.
- Hover-only elements (like delete buttons) need hover() first, then click_selector().
- Look for broken flows, wrong content, missing states, errors, or UX problems.
- CAPTCHA policy: read_page will flag '[automated-verification detected: ...]' when a CapTCHA (Turnstile/hCaptcha/reCAPTCHA) is on screen. If you see a human-verification checkbox ('Verify you are human', 'I\'m not a robot'), click it ONCE with click() on its label text, then wait and read the page. If a challenge still blocks the required flow, do NOT keep retrying: take a screenshot and honestly report the flow as blocked by the verification challenge in your final report.
- Identity providers: if you land on an identity-provider login page (accounts.google.com, appleid.apple.com, login.microsoftonline.com, a GitHub sign-in page), do NOT create accounts or enter credentials there. Navigate back to the app under test; if signing in through that provider is required by the task, report it as unverifiable in your final verdict.
- Forms: if clicking a submit button by its text times out, the label may differ from what you guessed. Re-read the page to see the real button text, or press('Enter') while the last field is focused as a fallback.
- Icon buttons (a social/share icon, a search magnifier, etc.) usually have NO visible text. read_page will show them as '(a:Twitter)' style hints, and get_links() lists them by their aria-label or alt. Use click_href() with a piece of their URL or click_selector('[aria-label="Twitter"]') rather than click() on text that isn't there.
- After clicking a social link, a new tab may open (get_links also reveals the target). Verify the destination loaded in the new tab, then close_tab() to return.
- Take a screenshot whenever you find something that looks like a bug.
- When done, give your verdict WITHOUT calling any more tools.
"""


def run_agent(url: str, goal: str) -> tuple[str, list, list]:
    client = OpenAI(
        api_key=os.getenv("OPENAI_API_KEY"),
        base_url=os.getenv("OPENAI_API_BASE") or None,
    )
    tool_schemas = TOOL_SCHEMAS
    model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

    messages: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Target app to test: {url}\n\nYour task: {goal}"},
    ]

    max_steps = int(os.getenv("QA_MAX_STEPS", "45"))

    actions: list[dict] = []
    screenshots: list[str] = []
    SHOT_DATA.clear()
    consecutive: dict[str, int] = {}
    seen: dict[str, int] = {}
    oauth_warned = False
    last_call = 0.0

    for _ in range(max_steps):
        response = None
        if _ > 0:
            wait = PACING_SECONDS - (time.time() - last_call)
            if wait > 0:
                print(f"\n[pacing - waiting {wait:.0f}s to stay under rate limits]")
                time.sleep(wait)
        for attempt in range(3):
            try:
                resp = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    tools=tool_schemas,
                    temperature=0,
                )
                last_call = time.time()
                response = resp.choices[0].message
                break
            except Exception as exc:
                status = getattr(getattr(exc, "response", None), "status_code", 0)
                body = getattr(exc, "body", None) or {}
                message = str(
                    body.get("error", {}).get("message", body.get("errors", ""))
                )
                match = re.search(r"try again in (\d+)m(\d+(?:\.\d+)?)s", message)
                if match:
                    wait = int(match.group(1)) * 60 + float(match.group(2))
                elif status in (413, 429):
                    wait = 45
                else:
                    wait = 0
                if wait:
                    if attempt == 2:
                        return (
                            f"Halted mid-run: the model provider rejected repeated requests "
                            f"(HTTP {status}). No final verdict was produced.\n\nDetail: {message[:400]}",
                            actions, screenshots,
                        )
                    print(f"\n[rate limit - waiting {wait:.0f}s before retrying]")
                    time.sleep(wait)
                    continue
                if attempt == 2:
                    return (
                        f"Halted mid-run: the model provider returned malformed responses "
                        f"three times. No final verdict was produced.\n\nDetail: {str(exc)[:400]}",
                        actions, screenshots,
                    )
                print(f"\n[retrying malformed response: {str(exc)[:100]}]")
                messages.append(
                    {
                        "role": "system",
                        "content": (
                            "Your previous reply was rejected because it was not a valid tool call "
                            "or final report. Reply now with a single tool call, or if you are truly "
                            "finished, your final report only."
                        ),
                    }
                )
        if response is None:
            return (
                "Halted mid-run: no model response could be obtained. "
                "No final verdict was produced.",
                actions, screenshots,
            )

        tool_calls = response.tool_calls or []
        if not tool_calls:
            return response.content or "No final answer produced.", actions, screenshots

        messages.append(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in tool_calls
                ],
            }
        )

        for tc in tool_calls:
            name = tc.function.name
            fn = TOOLS.get(name)
            if fn is None:
                messages.append(
                    {"role": "tool", "tool_call_id": tc.id, "content": f"Unknown tool: {name}"}
                )
                continue
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            guarded = False
            if name in ("scroll", "read_page"):
                consecutive[name] = consecutive.get(name, 0) + 1
                if consecutive[name] >= LOOP_CAP:
                    result = (
                        f"Loop guard: you have called {name} {LOOP_CAP}+ times in a row "
                        "without another action. Do something else next (click, type_text, "
                        "hover, press) or write your final report."
                    )
                    guarded = True
                    consecutive[name] = 0
            else:
                consecutive = {}
            if not guarded and name in REPEAT_GUARD_TOOLS:
                key = name + "|" + json.dumps(args, sort_keys=True)
                seen[key] = seen.get(key, 0) + 1
                if seen[key] >= REPEAT_LIMIT:
                    seen.clear()
                    result = (
                        f"Repeat guard: you have already called {name} with these exact "
                        f"arguments {REPEAT_LIMIT} times without a different action resolving "
                        "the situation. Do NOT call this again. Use read_page to inspect the "
                        "real page state and try a different element, or write your final report."
                    )
                    guarded = True
            if not guarded:
                try:
                    result = fn.invoke(args)
                    if name in SETTLE_TOOLS:
                        page.wait_for_timeout(SETTLE_MS)
                    opened = _sync_tabs()
                    if opened:
                        result = (
                            f"{result}\nThat action opened a new tab (NOW VIEWING: {opened[0]}). "
                            "You are now reading that new page. Use close_tab to return to the original page."
                        )
                except Exception as exc:
                    result = f"ERROR executing tool '{name}': {exc}"
            if not guarded:
                oauth = _oauth_warning()
                if oauth and not oauth_warned:
                    oauth_warned = True
                    result = (
                        f"{result}\n[WARNING: you are now on {oauth}, an identity-provider "
                        "login page. Do NOT create accounts or type personal information here. "
                        "Navigate back to the app under test; if signing in through this provider "
                        "is required for the task, report it as unverifiable in your final verdict.]"
                    )
                    messages.append(
                        {
                            "role": "system",
                            "content": (
                                "You are on an identity-provider login page ("
                                + oauth
                                + "). Do not create an account and do not type any personal "
                                "information there. Navigate back to the app under test. If "
                                "sign-in through this provider is required for the assigned "
                                "task, finish with a final report stating the flow is "
                                "unverifiable through this provider."
                            ),
                        }
                    )
            print(f"\n[{name} {args}]")
            actions.append({"name": name, "args": args, "result": str(result)})
            if name == "screenshot":
                marker = "Screenshot saved: "
                if result.startswith(marker):
                    screenshots.append(result[len(marker):].strip())
            messages.append(
                {"role": "tool", "tool_call_id": tc.id, "content": str(result)[:900]}
            )

        if len(messages) >= 24:
            messages = messages[:2] + messages[-12:]

    return (f"Reached {max_steps}-action limit without a final verdict.",
            actions, screenshots)


def main() -> None:
    parser = argparse.ArgumentParser(description="Agentic QA tester")
    parser.add_argument(
        "--url",
        default="https://demo.playwright.dev/todomvc",
        help="URL of the app to test",
    )
    parser.add_argument(
        "--goal",
        default=(
            "Test the todo app: add two todos, complete one, then delete one. "
            "Report what works, what breaks, and take screenshots of anything broken."
        ),
        help="What you want the agent to test",
    )
    parser.add_argument(
        "--format",
        choices=["md", "html", "pdf", "all"],
        default=["md"],
        action="append",
        help="Report format(s) to generate (repeatable; 'all' = md, html, pdf)",
    )
    args = parser.parse_args()

    if not os.getenv("OPENAI_API_KEY"):
        print("Missing OPENAI_API_KEY.")
        print("Copy .env.example to .env and paste in your key.")
        return

    _load_session(args.url)

    print(f"\n>>> Testing {args.url}")
    print(f">>> Goal: {args.goal}")
    print(f">>> Model: {os.getenv('OPENAI_MODEL')}\n")

    final, actions, screenshots = run_agent(args.url, args.goal)

    print("\n=== FINAL REPORT ===")
    print(final)

    _save_session(args.url)

    browser.close()
    _pw.stop()

    report_data = {
        "url": args.url,
        "goal": args.goal,
        "model": os.getenv("OPENAI_MODEL"),
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "actions": actions,
        "screenshots": screenshots,
        "shot_data": dict(SHOT_DATA),
        "final_report": final,
    }

    requested = args.format or ["md"]
    formats = ["md", "html", "pdf"] if "all" in requested else requested
    report_dir = Path(os.getenv("QA_REPORT_DIR", "reports"))
    for fmt in formats:
        files = generate_report(report_data, fmt, report_dir)
        for path in files:
            print(f"Report written: {path}")


if __name__ == "__main__":
    main()