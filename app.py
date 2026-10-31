import hashlib
import json
import logging
import os
import re
import urllib.parse
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from threading import Lock

from bs4 import BeautifulSoup
from curl_cffi import requests as curl_requests
from flask import Flask, Response, abort, has_request_context, request

from credential_crypto import CredentialError, decrypt_credentials_token


app = Flask(__name__)
app.logger.setLevel(logging.INFO)

BASE_URL = os.getenv("MINI4K_BASE_URL", "https://www.mini4k.com").rstrip("/")
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://flaresolverr:8191/v1")
FLARESOLVERR_MAX_TIMEOUT = max(
    60000, min(int(os.getenv("FLARESOLVERR_MAX_TIMEOUT", "180000")), 600000)
)
FLARESOLVERR_HTTP_TIMEOUT = max(
    (FLARESOLVERR_MAX_TIMEOUT // 1000) + 30,
    int(os.getenv("FLARESOLVERR_HTTP_TIMEOUT", "210")),
)
FLARESOLVERR_SESSION_ID = os.getenv(
    "FLARESOLVERR_SESSION_ID", "mini4k-torznab"
).strip() or "mini4k-torznab"
FLARESOLVERR_SESSION_TTL_MINUTES = max(
    10, min(int(os.getenv("FLARESOLVERR_SESSION_TTL_MINUTES", "1440")), 10080)
)
BROWSER_LOCK_TIMEOUT = max(
    1, min(int(os.getenv("BROWSER_LOCK_TIMEOUT", "30")), 300)
)
API_KEY = os.getenv("API_KEY", "").strip()
FETCH_MODE = os.getenv("FETCH_MODE", "flaresolverr").strip().lower()
AUTH_STATE_FILE = os.getenv("MINI4K_AUTH_STATE_FILE", "/data/auth_state.json")
COOKIE_FILE = os.getenv("MINI4K_COOKIE_FILE", "/data/mini4k-cookie.txt")
USER_AGENT_FILE = os.getenv("MINI4K_USER_AGENT_FILE", "/data/mini4k-user-agent.txt")
ENCRYPTED_CREDENTIALS_TOKEN = os.getenv("MINI4K_CREDENTIALS_TOKEN", "").strip()
CREDENTIALS_KEY = os.getenv("MINI4K_CREDENTIALS_KEY", "").strip()
AUTO_LOGIN = os.getenv("MINI4K_AUTO_LOGIN", "true").strip().lower() in {
    "1", "true", "yes", "on",
}
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)
MAX_WORKS = max(1, min(int(os.getenv("MAX_WORKS", "4")), 10))
MAX_RESULTS = max(1, min(int(os.getenv("MAX_RESULTS", "100")), 200))
TEST_QUERY_MOVIE = os.getenv("TEST_QUERY_MOVIE", os.getenv("TEST_QUERY", "沙丘")).strip() or "沙丘"
TEST_QUERY_TV = os.getenv("TEST_QUERY_TV", "怪奇物语").strip() or "怪奇物语"
_browser_lock = Lock()


def xml_escape(value):
    value = str(value or "")
    return (value.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;").replace("'", "&apos;"))


def read_cookie_header():
    try:
        with open(COOKIE_FILE, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except FileNotFoundError:
        value = os.getenv("MINI4K_COOKIE", "").strip()
    value = re.sub(r"^cookie\s*:\s*", "", value, flags=re.I)
    return " ".join(value.splitlines()).strip()


def read_user_agent():
    try:
        value = Path(USER_AGENT_FILE).read_text(encoding="utf-8").strip()
        return value or DEFAULT_USER_AGENT
    except FileNotFoundError:
        return os.getenv("MINI4K_USER_AGENT", DEFAULT_USER_AGENT).strip()


def read_credentials():
    return decrypt_credentials_token(ENCRYPTED_CREDENTIALS_TOKEN, CREDENTIALS_KEY)


def flare_cookies():
    cookies = []
    for part in read_cookie_header().split(";"):
        if "=" not in part:
            continue
        name, value = part.strip().split("=", 1)
        if name:
            lowered = name.lower()
            # Cloudflare clearance is normally bound to the browser/IP that
            # obtained it. Let NAS-side FlareSolverr create its own clearance,
            # while forwarding only Mini4K's account-login cookies.
            if lowered == "cf_clearance" or lowered.startswith("__cf") or lowered.startswith("cf_chl"):
                continue
            cookies.append({"name": name, "value": value})
    return cookies


def save_account_cookies(cookies):
    values = []
    for cookie in cookies or []:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "")
        domain = str(cookie.get("domain") or "").lower()
        lowered = name.lower()
        if not name or (domain and "mini4k.com" not in domain):
            continue
        if lowered == "cf_clearance" or lowered.startswith("__cf") or lowered.startswith("cf_chl"):
            continue
        values.append(f"{name}={value}")
    if not values:
        raise RuntimeError("Mini4K login succeeded but returned no account cookies")
    target = Path(COOKIE_FILE)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text("; ".join(values), encoding="utf-8")
    os.replace(temporary, target)


def flaresolverr_call(payload, timeout=None):
    if timeout is None:
        timeout = FLARESOLVERR_HTTP_TIMEOUT
    req = urllib.request.Request(
        FLARESOLVERR_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return json.load(response)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")[:1000].strip()
        raise RuntimeError(f"FlareSolverr HTTP {exc.code}: {body or exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"Cannot reach FlareSolverr: {exc}") from exc


def flaresolverr_solution(payload):
    result = flaresolverr_call(payload)
    if result.get("status") != "ok":
        raise RuntimeError(result.get("message") or "FlareSolverr request failed")
    solution = result.get("solution") or {}
    html = solution.get("response") or ""
    status = int(solution.get("status") or 0)
    if status >= 400 or not html:
        raise RuntimeError(f"Mini4K returned HTTP {status}")
    return solution, html


def flaresolverr_login(session_id):
    if not AUTO_LOGIN:
        raise RuntimeError("Mini4K cookie is missing or expired")
    username, password = read_credentials()
    if not username or not password:
        raise RuntimeError(
            "Mini4K cookie expired and automatic login credentials are not configured"
        )
    login_url = f"{BASE_URL}/user/login"
    base_payload = {
        "url": login_url,
        "maxTimeout": FLARESOLVERR_MAX_TIMEOUT,
        "session": session_id,
        "session_ttl_minutes": FLARESOLVERR_SESSION_TTL_MINUTES,
        "cookies": flare_cookies(),
    }
    solution, html = flaresolverr_solution({"cmd": "request.get", **base_payload})
    if "user-logged-in" in html:
        save_account_cookies(solution.get("cookies"))
        return

    soup = BeautifulSoup(html, "html.parser")
    form = soup.select_one("form.user-login-form, form[action*='/user/login']")
    if not form:
        raise RuntimeError("Mini4K automatic login form was not found")
    post_fields = {}
    for field in form.select("input[name]"):
        field_type = (field.get("type") or "text").lower()
        if field_type in {"checkbox", "radio"} and not field.has_attr("checked"):
            continue
        post_fields[field.get("name")] = field.get("value") or ""
    username_field = form.select_one(
        "input[name='name'], input[name='username'], input[type='email'], input[type='text']"
    )
    password_field = form.select_one("input[name='pass'], input[name='password'], input[type='password']")
    if not username_field or not password_field:
        raise RuntimeError("Mini4K automatic login fields were not found")
    post_fields[username_field.get("name")] = username
    post_fields[password_field.get("name")] = password
    action_url = absolute_url(form.get("action") or "/user/login")
    post_payload = {
        "cmd": "request.post",
        "url": action_url,
        "maxTimeout": FLARESOLVERR_MAX_TIMEOUT,
        "session": session_id,
        "session_ttl_minutes": FLARESOLVERR_SESSION_TTL_MINUTES,
        "postData": urllib.parse.urlencode(post_fields),
    }
    solution, html = flaresolverr_solution(post_payload)
    if "user-logged-in" not in html:
        raise RuntimeError("Mini4K automatic login failed; check credentials or login challenge")
    save_account_cookies(solution.get("cookies"))
    app.logger.info("Mini4K account session renewed automatically")


def flaresolverr_fetch(url, session_id=None, allow_login=True, include_solution=False):
    payload = {
        "cmd": "request.get",
        "url": url,
        "maxTimeout": FLARESOLVERR_MAX_TIMEOUT,
        "cookies": flare_cookies(),
    }
    if session_id:
        payload["session"] = session_id
        payload["session_ttl_minutes"] = FLARESOLVERR_SESSION_TTL_MINUTES
    solution, html = flaresolverr_solution(payload)
    if "user-logged-in" not in html and "/user/login" in html:
        if allow_login and session_id:
            flaresolverr_login(session_id)
            return flaresolverr_fetch(
                url,
                session_id,
                allow_login=False,
                include_solution=include_solution,
            )
        raise RuntimeError("Mini4K cookie is missing or expired")
    if include_solution:
        return solution, html
    return html


class Mini4KSession:
    """One browser context per Torznab query, based on mini4k_downloader's auth-state flow."""

    def __init__(self):
        self._lock_acquired = False
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._flare_session = None

    def __enter__(self):
        if FETCH_MODE == "flaresolverr":
            if not _browser_lock.acquire(timeout=BROWSER_LOCK_TIMEOUT):
                raise RuntimeError(
                    "Another Mini4K search is still running; retry shortly"
                )
            self._lock_acquired = True
            try:
                # Keep one named browser session alive across Prowlarr requests so
                # Cloudflare does not need to be solved again for every search.
                self._flare_session = FLARESOLVERR_SESSION_ID
                result = flaresolverr_call({"cmd": "sessions.create", "session": self._flare_session})
                message = str(result.get("message") or "")
                if result.get("status") != "ok" and "already" not in message.lower():
                    raise RuntimeError(result.get("message") or "Cannot create FlareSolverr session")
                return self
            except Exception:
                self._release_browser_lock()
                raise
        if FETCH_MODE != "playwright":
            raise RuntimeError("FETCH_MODE must be 'playwright' or 'flaresolverr'")
        if not _browser_lock.acquire(timeout=BROWSER_LOCK_TIMEOUT):
            raise RuntimeError(
                "Another Mini4K search is still running; retry shortly"
            )
        self._lock_acquired = True
        try:
            from playwright.sync_api import sync_playwright

            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                args=["--disable-dev-shm-usage", "--no-sandbox"],
            )
            options = {"user_agent": read_user_agent()}
            if Path(AUTH_STATE_FILE).is_file():
                options["storage_state"] = AUTH_STATE_FILE
            self._context = self._browser.new_context(**options)
            if not Path(AUTH_STATE_FILE).is_file():
                cookies = flare_cookies()
                if cookies:
                    self._context.add_cookies([{**cookie, "url": BASE_URL} for cookie in cookies])
            self._page = self._context.new_page()
            return self
        except Exception:
            self._release_browser_lock()
            raise

    def _release_browser_lock(self):
        if self._lock_acquired:
            self._lock_acquired = False
            _browser_lock.release()

    def __exit__(self, exc_type, exc, traceback):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
            if self._playwright:
                self._playwright.stop()
        finally:
            self._release_browser_lock()

    def get(self, url):
        if FETCH_MODE == "flaresolverr":
            return flaresolverr_fetch(url, self._flare_session)
        if not self._page:
            raise RuntimeError("Playwright browser session was not started")
        try:
            self._page.goto(url, wait_until="domcontentloaded", timeout=60000)
            self._page.wait_for_timeout(1200)
        except Exception as exc:
            raise RuntimeError(f"Playwright could not load Mini4K: {exc}") from exc
        title = self._page.title().lower()
        html = self._page.content()
        if "just a moment" in title or "cloudflare" in title or "正在執行安全驗證" in html:
            raise RuntimeError("Cloudflare rejected the saved browser state")
        if "user-logged-in" not in html:
            raise RuntimeError("Mini4K auth_state or Cookie is missing/expired")
        return html

    def get_with_solution(self, url):
        if FETCH_MODE == "flaresolverr":
            return flaresolverr_fetch(
                url,
                self._flare_session,
                include_solution=True,
            )
        html = self.get(url)
        cookies = self._context.cookies() if self._context else []
        return {"cookies": cookies, "userAgent": read_user_agent()}, html


def absolute_url(value):
    return urllib.parse.urljoin(BASE_URL + "/", value or "")


def parse_size(value):
    match = re.search(r"([\d.]+)\s*(KB|MB|GB|TB)", value or "", re.I)
    if not match:
        return 0
    amount = float(match.group(1))
    powers = {"KB": 1, "MB": 2, "GB": 3, "TB": 4}
    return int(amount * (1024 ** powers[match.group(2).upper()]))


def parse_relative_date(value):
    now = datetime.now(timezone.utc)
    text = (value or "").strip()
    patterns = [
        (r"(\d+)\s*分钟", "minutes"), (r"(\d+)\s*小[时時]", "hours"),
        (r"(\d+)\s*天", "days"), (r"(\d+)\s*[周週]", "weeks"),
        (r"(\d+)\s*[个個]?月", "months"), (r"(\d+)\s*年", "years"),
    ]
    for pattern, unit in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        number = int(match.group(1))
        days = number * 30 if unit == "months" else number * 365 if unit == "years" else 0
        delta = timedelta(**({unit: number} if unit in {"minutes", "hours", "days", "weeks"} else {"days": days}))
        return now - delta
    return now


def optional_nonnegative_int(value):
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def normalize_tv_query(query, season=None, episode=None):
    """Remove Sonarr season/episode tokens before searching Mini4K."""
    cleaned = query or ""

    def remove_token(text, match, includes_episode=False):
        # Release-style queries often append the episode title after SxxEyy or
        # 4x02. Mini4K searches work best with the series title alone.
        if includes_episode:
            return text[:match.start()] if match.start() else text[match.end():]
        return f"{text[:match.start()]} {text[match.end():]}"

    token = re.search(
        r"(?i)(?<![a-z0-9])s(\d{1,2})(?:[ ._-]*e(\d{1,3}))?(?![a-z0-9])",
        cleaned,
    )
    if token:
        if season is None:
            season = int(token.group(1))
        if episode is None and token.group(2) is not None:
            episode = int(token.group(2))
        cleaned = remove_token(cleaned, token, token.group(2) is not None)

    x_token = re.search(r"(?i)(?<![a-z0-9])(\d{1,2})x(\d{1,3})(?!\d)", cleaned)
    if x_token:
        if season is None:
            season = int(x_token.group(1))
        if episode is None:
            episode = int(x_token.group(2))
        cleaned = remove_token(cleaned, x_token, includes_episode=True)

    season_token = re.search(r"(?i)(?<![a-z0-9])season[ ._-]*(\d{1,2})(?!\d)", cleaned)
    if season_token:
        if season is None:
            season = int(season_token.group(1))
        cleaned = f"{cleaned[:season_token.start()]} {cleaned[season_token.end():]}"

    # An episode-only token is uncommon, but tv-search supports it.
    episode_token = re.search(r"(?i)(?<![a-z0-9])e(?:p)?[ ._-]*(\d{1,3})(?!\d)", cleaned)
    if episode_token:
        if episode is None:
            episode = int(episode_token.group(1))
        cleaned = remove_token(cleaned, episode_token, includes_episode=True)

    cleaned = re.sub(r"\s+", " ", cleaned).strip(" ._-")
    return cleaned or query, season, episode


def release_matches_season_episode(title, season=None, episode=None):
    if season is None and episode is None:
        return True
    text = title or ""
    if season is not None and episode is not None:
        combined_patterns = (
            rf"(?i)(?<![a-z0-9])s0*{season}[ ._-]*e0*{episode}(?!\d)",
            rf"(?i)(?<!\d)0*{season}x0*{episode}(?!\d)",
        )
        if any(re.search(pattern, text) for pattern in combined_patterns):
            return True
        # Fall through for titles that spell out the season and episode.
    season_matched = season is None
    if season is not None:
        chinese_seasons = {
            1: "一", 2: "二", 3: "三", 4: "四", 5: "五",
            6: "六", 7: "七", 8: "八", 9: "九", 10: "十",
        }
        chinese_season = chinese_seasons.get(season)
        season_patterns = (
            rf"(?i)(?<![a-z0-9])s0*{season}(?:[ ._-]*e\d+|\b)",
            rf"(?i)(?<![a-z0-9])season[ ._-]*0*{season}(?!\d)",
            rf"第\s*0*{season}\s*季",
        )
        season_matched = any(re.search(pattern, text) for pattern in season_patterns)
        if chinese_season:
            season_matched = season_matched or bool(
                re.search(rf"第\s*{chinese_season}\s*季", text)
            )
        if not season_matched:
            return False
    if episode is not None:
        episode_patterns = (
            rf"(?i)(?<![a-z0-9])(?:s\d{{1,2}}[ ._-]*)?e(?:p)?0*{episode}(?!\d)",
            rf"(?i)(?<!\d)\d{{1,2}}x0*{episode}(?!\d)",
            rf"第\s*0*{episode}\s*集",
        )
        if any(re.search(pattern, text) for pattern in episode_patterns):
            return True
        # Mini4K sometimes has no individual episode but does have a magnet
        # for the complete season. Return that season pack so Sonarr can use it.
        has_episode_marker = bool(re.search(
            r"(?i)(?:s\d{1,2}[ ._-]*e\d{1,3}|(?<!\d)\d{1,2}x\d{1,3}|第\s*\d+\s*集)",
            text,
        ))
        if season is not None and season_matched and not has_episode_marker:
            return True
        return False
    return True


def work_links(fetch_html, query, kind, max_works=None):
    url = f"{BASE_URL}/search?{urllib.parse.urlencode({'text': query})}"
    soup = BeautifulSoup(fetch_html(url), "html.parser")
    prefixes = ["/movies/"] if kind == "movie" else ["/shows/"] if kind == "tv" else ["/movies/", "/shows/"]
    found = []
    seen = set()
    limit = MAX_WORKS if max_works is None else max(1, min(max_works, MAX_WORKS))
    for anchor in soup.select("main a[href]"):
        href = anchor.get("href", "")
        if not any(href.startswith(prefix) for prefix in prefixes) or href in seen:
            continue
        seen.add(href)
        found.append(absolute_url(href))
        if len(found) >= limit:
            break
    return found


def release_category(work_url, title, tab_name=""):
    is_tv = "/shows/" in work_url
    text = f"{title} {tab_name}".lower()
    if "2160" in text or "4k" in text:
        return 5045 if is_tv else 2045
    if "1080" in text:
        return 5040 if is_tv else 2040
    if "720" in text:
        return 5040 if is_tv else 2040
    return 5000 if is_tv else 2000


def parse_releases(fetch_html, work_url):
    soup = BeautifulSoup(fetch_html(work_url), "html.parser")
    releases = []
    for row in soup.select("table tr"):
        title_link = row.select_one('td.views-field-title a[href^="/torrents/"]')
        if not title_link:
            continue
        title = (title_link.get("title") or title_link.get_text(" ", strip=True)).strip()
        magnet_node = row.select_one("[data-link^='magnet:'], [data-clipboard-text^='magnet:']")
        torrent_node = row.select_one("a[href$='.torrent']")
        if magnet_node:
            link = magnet_node.get("data-link") or magnet_node.get("data-clipboard-text")
            link = link.replace("&amp;", "&")
            if not link.lower().startswith("magnet:?xt=urn:btih:"):
                continue
        elif torrent_node:
            link = absolute_url(torrent_node.get("href"))
            if not urllib.parse.urlparse(link).path.lower().endswith(".torrent"):
                continue
        else:
            continue
        tab = row.find_parent(attrs={"data-tab": True})
        tab_name = tab.get("data-tab", "") if tab else ""
        size_node = row.select_one("td.views-field-nothing:not(.views-field-nothing-1)")
        date_node = row.select_one("td.views-field-changed")
        details = absolute_url(title_link.get("href"))
        releases.append({
            "title": title,
            "link": link,
            "details": details,
            "size": parse_size(size_node.get_text(" ", strip=True) if size_node else ""),
            "date": parse_relative_date(date_node.get_text(" ", strip=True) if date_node else ""),
            "category": release_category(work_url, title, tab_name),
        })
    return releases


def caps_xml():
    return """<?xml version="1.0" encoding="UTF-8"?>
<caps>
  <server version="2.0" title="Mini4K Torznab" />
  <limits max="100" default="50" />
  <searching>
    <search available="yes" supportedParams="q" />
    <tv-search available="yes" supportedParams="q,season,ep" />
    <movie-search available="yes" supportedParams="q" />
  </searching>
  <categories>
    <category id="2000" name="Movies"><subcat id="2030" name="SD" /><subcat id="2040" name="HD" /><subcat id="2045" name="UHD" /></category>
    <category id="5000" name="TV"><subcat id="5030" name="SD" /><subcat id="5040" name="HD" /><subcat id="5045" name="UHD" /></category>
  </categories>
</caps>"""


def rss_xml(releases, max_results=None, offset=0):
    items = []
    limit = MAX_RESULTS if max_results is None else max(1, min(max_results, MAX_RESULTS))
    offset = max(0, offset)
    for item in releases[offset:offset + limit]:
        link = item["link"]
        torrent_match = re.search(r"/torrents/(\d+)(?:$|[/?#])", item.get("details") or "")
        link_path = urllib.parse.urlparse(link).path.lower()
        # Magnets must be passed through unchanged. Some Mini4K detail pages
        # have no .torrent button at all, so forcing every result through the
        # authenticated download proxy makes otherwise valid magnets fail.
        # Only a release already identified as a direct .torrent uses the
        # bridge proxy to preserve Mini4K/Cloudflare authentication.
        if torrent_match and link_path.endswith(".torrent") and has_request_context():
            link = urllib.parse.urljoin(
                request.host_url,
                f"v2/download/{torrent_match.group(1)}",
            )
            link = f"{link}?apikey={urllib.parse.quote(API_KEY, safe='')}"
        guid = hashlib.sha256(link.encode("utf-8")).hexdigest()
        items.append(f"""<item>
  <title>{xml_escape(item['title'])}</title>
  <guid isPermaLink="false">mini4k-{guid}</guid>
  <link>{xml_escape(link)}</link>
  <comments>{xml_escape(item['details'])}</comments>
  <pubDate>{format_datetime(item['date'])}</pubDate>
  <category>{item['category']}</category>
  <enclosure url="{xml_escape(link)}" length="{item['size']}" type="application/x-bittorrent" />
  <torznab:attr name="category" value="{item['category']}" />
  <torznab:attr name="size" value="{item['size']}" />
  <torznab:attr name="seeders" value="1" />
  <torznab:attr name="peers" value="1" />
</item>""")
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:atom="http://www.w3.org/2005/Atom" xmlns:torznab="http://torznab.com/schemas/2015/feed">
<channel><title>Mini4K</title><description>Mini4K Torznab bridge</description><link>{xml_escape(BASE_URL)}</link>
{''.join(items)}
</channel></rss>"""


def check_api_key():
    if not API_KEY:
        abort(503, description="API_KEY is not configured")
    if request.args.get("apikey") != API_KEY:
        abort(401)


def torrent_download_url(html):
    soup = BeautifulSoup(html, "html.parser")
    node = soup.select_one("a[href$='.torrent']")
    if not node or not node.get("href"):
        raise RuntimeError("Mini4K torrent download link was not found")
    url = absolute_url(node.get("href"))
    if not urllib.parse.urlparse(url).path.lower().endswith(".torrent"):
        raise RuntimeError("Mini4K returned an invalid torrent download link")
    return url


def solution_cookies(solution):
    cookies = {}
    for cookie in [*flare_cookies(), *((solution or {}).get("cookies") or [])]:
        name = str(cookie.get("name") or "").strip()
        value = str(cookie.get("value") or "").strip()
        if name and value:
            cookies[name] = value
    return cookies


def fetch_torrent_bytes(url, solution, referer):
    headers = {
        "Referer": referer,
        "User-Agent": (solution or {}).get("userAgent") or read_user_agent(),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
    }
    try:
        response = curl_requests.get(
            url,
            headers=headers,
            cookies=solution_cookies(solution),
            impersonate="chrome",
            timeout=FLARESOLVERR_HTTP_TIMEOUT,
            allow_redirects=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Cannot download Mini4K torrent file: {exc}") from exc
    if response.status_code >= 400:
        raise RuntimeError(
            f"Mini4K torrent download returned HTTP {response.status_code}"
        )
    data = response.content
    if len(data) > 20 * 1024 * 1024:
        raise RuntimeError("Mini4K torrent file is unexpectedly large")
    if not data.startswith(b"d") or b"4:info" not in data:
        raise RuntimeError("Mini4K download did not return a valid torrent file")
    return data


@app.get("/v2/download/<int:torrent_id>")
def download_torrent(torrent_id):
    check_api_key()
    details_url = f"{BASE_URL}/torrents/{torrent_id}"
    try:
        with Mini4KSession() as session:
            solution, html = session.get_with_solution(details_url)
            download_url = torrent_download_url(html)
            data = fetch_torrent_bytes(download_url, solution, details_url)
    except RuntimeError as exc:
        app.logger.error("Mini4K torrent download failed for id=%d: %s", torrent_id, exc)
        return Response(f"Mini4K bridge error: {exc}", status=502, mimetype="text/plain")
    app.logger.info("Mini4K torrent download id=%d bytes=%d", torrent_id, len(data))
    return Response(
        data,
        mimetype="application/x-bittorrent",
        headers={"Content-Disposition": f'attachment; filename="mini4k-{torrent_id}.torrent"'},
    )


@app.get("/health")
@app.get("/v2/health")
def health():
    try:
        username, password = read_credentials()
        credential_status = "encrypted_env"
    except (CredentialError, RuntimeError):
        username, password = "", ""
        credential_status = "missing_or_invalid"
    return {
        "status": "ok",
        "fetchMode": FETCH_MODE,
        "flaresolverrMaxTimeoutMs": FLARESOLVERR_MAX_TIMEOUT,
        "flaresolverrHttpTimeoutSec": FLARESOLVERR_HTTP_TIMEOUT,
        "automaticLoginConfigured": bool(
            AUTO_LOGIN and username and password
        ),
        "credentialStatus": credential_status,
    }


@app.get("/api")
@app.get("/v2/api")
def api():
    action = request.args.get("t", "caps").lower()
    if action == "caps":
        return Response(caps_xml(), mimetype="application/xml")
    check_api_key()
    if action == "get":
        abort(404)
    if action not in {"search", "tvsearch", "movie"}:
        abort(400)
    # Prowlarr validates Generic Torznab with an empty query and expects at
    # least one release. Use a configurable known title only for that case.
    requested_query = (request.args.get("q") or "").strip()
    is_validation = not requested_query
    categories = {
        int(value)
        for value in (request.args.get("cat") or "").split(",")
        if value.strip().isdigit()
    }
    has_movie_category = any(2000 <= category < 3000 for category in categories)
    has_tv_category = any(5000 <= category < 6000 for category in categories)
    if action == "tvsearch" or (has_tv_category and not has_movie_category):
        kind = "tv"
    elif action == "movie" or (has_movie_category and not has_tv_category):
        kind = "movie"
    else:
        kind = "all"
    default_query = TEST_QUERY_TV if kind == "tv" else TEST_QUERY_MOVIE
    query = requested_query or default_query
    season = optional_nonnegative_int(request.args.get("season"))
    episode = optional_nonnegative_int(request.args.get("ep"))
    site_query, season, episode = normalize_tv_query(query, season, episode)
    requested_limit = optional_nonnegative_int(request.args.get("limit"))
    result_limit = requested_limit or MAX_RESULTS
    if is_validation:
        result_limit = min(result_limit, 20)
    result_limit = max(1, min(result_limit, MAX_RESULTS))
    offset = optional_nonnegative_int(request.args.get("offset")) or 0
    releases = []
    try:
        with Mini4KSession() as session:
            for work_url in work_links(session.get, site_query, kind, 1 if is_validation else MAX_WORKS):
                releases.extend(parse_releases(session.get, work_url))
    except RuntimeError as exc:
        app.logger.error("Mini4K search failed: %s", exc)
        return Response(f"Mini4K bridge error: {exc}", status=502, mimetype="text/plain")
    fetched_count = len(releases)
    releases = [
        item
        for item in releases
        if release_matches_season_episode(item["title"], season, episode)
    ]
    returned = len(releases[offset:offset + result_limit])
    app.logger.info(
        "Torznab %s query=%r site_query=%r season=%r episode=%r works<=%d fetched=%d matched=%d offset=%d returned=%d",
        action,
        query,
        site_query,
        season,
        episode,
        MAX_WORKS,
        fetched_count,
        len(releases),
        offset,
        returned,
    )
    return Response(
        rss_xml(releases, result_limit, offset),
        mimetype="application/rss+xml",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8787)

