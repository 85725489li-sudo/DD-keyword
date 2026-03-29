import os
import re
import json
import time
import random
import requests
from collections import Counter
from playwright.sync_api import sync_playwright
from database import get_cookies, save_cookies, add_log, get_internal_id_cache, save_internal_id_cache

DD_EMAIL = os.environ.get("DD_EMAIL", "")
DD_PASSWORD = os.environ.get("DD_PASSWORD", "")
IS_SERVER = os.environ.get("RENDER", "") or os.environ.get("HEADLESS", "")

ITUNES_API = "https://itunes.apple.com/lookup"


# ─────────────────────────────────────────
# iTunes API
# ─────────────────────────────────────────

def get_app_info_itunes(app_id: str, country_code: str) -> dict:
    try:
        r = requests.get(ITUNES_API, params={"id": app_id, "country": country_code}, timeout=10)
        data = r.json()
        if data.get("resultCount", 0) > 0:
            result = data["results"][0]
            return {
                "name": result.get("trackName", ""),
                "icon": result.get("artworkUrl100", result.get("artworkUrl60", "")),
            }
    except Exception:
        pass
    return {"name": app_id, "icon": ""}


# ─────────────────────────────────────────
# 快速路径：直接 HTTP 请求（无浏览器）
# ─────────────────────────────────────────

def cookies_to_dict(cookies: list) -> dict:
    return {c["name"]: c["value"] for c in cookies}


def get_internal_id_fast(app_store_id: str, session: requests.Session, log) -> str | None:
    """直接请求搜索页 HTML 提取内部 ID"""
    cached = get_internal_id_cache(app_store_id)
    if cached:
        log(f"  内部 ID（缓存）: {cached}")
        return cached

    try:
        r = session.get(
            f"https://app.diandian.com/search?keyword={app_store_id}&type=app",
            timeout=15,
        )
        matches = re.findall(r'/app/([a-z0-9]{10,})/ios', r.text)
        counter = Counter(matches)
        candidates = [(iid, cnt) for iid, cnt in counter.most_common() if cnt <= 4]
        iid = candidates[0][0] if candidates else (counter.most_common(1)[0][0] if counter else None)
        if iid:
            log(f"  内部 ID: {iid}")
            save_internal_id_cache(app_store_id, iid)
            return iid
    except Exception as e:
        log(f"  搜索内部 ID 失败: {e}")
    return None


def fetch_keywords_fast(internal_id: str, country_id: str, filters: dict, session: requests.Session, log) -> list | None:
    """直接调用点点数据 API 获取关键词（无浏览器，速度快 10 倍）"""
    # 构造 API URL
    api_url = f"https://api.diandian.com/app/{internal_id}/word/analysi/detail"
    params = {"market": 1, "country_id": country_id}

    try:
        r = session.get(api_url, params=params, timeout=20)
        if r.status_code == 401 or r.status_code == 403:
            log("  Session 失效，需要重新登录")
            return None
        data = r.json()
        if data.get("code") != 0:
            log(f"  API 返回错误: {data.get('msg', data.get('code'))}")
            return None

        raw = data.get("data", [])
        items = raw if isinstance(raw, list) else raw.get("list", raw.get("data", []))
        log(f"  API 返回 {len(items)} 个关键词")
        return _filter_keywords(items, filters)

    except Exception as e:
        log(f"  直接请求失败: {e}")
        return None


def _filter_keywords(items, filters):
    results = []
    for item in items:
        if not isinstance(item, list) or len(item) < 8:
            continue
        keyword = item[0]
        rank = item[1] if item[1] is not None else 9999
        search_index = item[3] if item[3] is not None else 0
        search_results = item[4] if item[4] is not None else 0
        popularity = item[7] if item[7] is not None else 0

        if (rank <= filters.get("rank_max", 10) and
                popularity >= filters.get("popularity_min", 0) and
                search_index >= filters.get("search_index_min", 0) and
                search_results >= filters.get("search_results_min", 0)):
            results.append({
                "keyword": keyword,
                "rank": rank,
                "popularity": popularity,
                "search_index": search_index,
                "search_results": search_results,
            })
    results.sort(key=lambda x: x["popularity"], reverse=True)
    return results


# ─────────────────────────────────────────
# 慢速路径：Playwright（备用）
# ─────────────────────────────────────────

def fetch_keywords_playwright(app_store_id: str, internal_id: str, country_id: str, filters: dict, cookies: list, log) -> list:
    """Playwright 浏览器方式（当直接请求失败时使用）"""
    log("  切换到浏览器模式...")
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=bool(IS_SERVER),
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--no-blink-features=AutomationControlled"],
        )
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )
        if cookies:
            try:
                context.add_cookies(cookies)
            except Exception:
                pass

        page = context.new_page()
        captured = {}

        def on_response(response):
            url = response.url
            if "api.diandian.com" in url and "word/analysi/detail" in url and response.status == 200:
                try:
                    body = response.json()
                    if body.get("code") == 0:
                        captured["data"] = body.get("data", [])
                        log("  ★ 捕获关键词数据")
                except Exception:
                    pass

        page.on("response", on_response)

        aso_url = f"https://app.diandian.com/app/{internal_id}/ios-aso?market=1&country={country_id}&section=keyword"
        page.goto(aso_url, timeout=30000, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)

        # 点关键词 tab
        for sel in ["text=关键词", "li:has-text('关键词')"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    break
            except Exception:
                pass

        # 等待 API 响应（最多 30 秒）
        for _ in range(30):
            if "data" in captured:
                break
            page.wait_for_timeout(1000)

        context.close()
        browser.close()

        if "data" in captured:
            raw = captured["data"]
            items = raw if isinstance(raw, list) else raw.get("list", [])
            results = _filter_keywords(items, filters)
        else:
            log("  ⚠️ 浏览器模式也未获取到数据")

    return results


# ─────────────────────────────────────────
# 主入口
# ─────────────────────────────────────────

def run_scrape_job(job: dict, log_fn):
    app_ids = json.loads(job["app_ids"])
    country_id = job["country_id"]
    country_code = job["country_code"]
    filters = {
        "rank_max": job.get("rank_max", 10),
        "popularity_min": job.get("popularity_min", 0),
        "search_index_min": job.get("search_index_min", 0),
        "search_results_min": job.get("search_results_min", 0),
    }

    # 构造带 Cookie 的 requests Session
    cookies = get_cookies()
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://app.diandian.com/",
        "Accept": "application/json, text/plain, */*",
    })
    if cookies:
        session.cookies.update(cookies_to_dict(cookies))
        log_fn("已加载 Session Cookie")

    # 获取 iTunes App 信息
    log_fn(f"获取 {len(app_ids)} 个 App 信息...")
    app_info_map = {}
    for app_id in app_ids:
        info = get_app_info_itunes(app_id, country_code)
        app_info_map[app_id] = info
        log_fn(f"  {info.get('name') or app_id} ({app_id})")

    all_results = []

    for i, app_id in enumerate(app_ids):
        log_fn(f"\n[{i+1}/{len(app_ids)}] 处理 App: {app_id}")
        app_info = app_info_map[app_id]

        # 1. 获取内部 ID（直接请求）
        internal_id = get_internal_id_fast(app_id, session, log_fn)
        if not internal_id:
            log_fn(f"  ⚠️ 未找到内部 ID，跳过")
            continue

        # 2. 直接 API 请求关键词（快速路径）
        keywords = fetch_keywords_fast(internal_id, country_id, filters, session, log_fn)

        # 3. 若直接请求失败，降级到 Playwright
        if keywords is None:
            log_fn("  直接请求失败，降级到浏览器模式...")
            keywords = fetch_keywords_playwright(app_id, internal_id, country_id, filters, cookies, log_fn)

        for kw in keywords:
            kw["app_id"] = app_id
            kw["app_name"] = app_info.get("name", app_id)
            kw["app_icon"] = app_info.get("icon", "")

        all_results.extend(keywords)
        log_fn(f"  ✅ 完成，{len(keywords)} 个关键词")

    log_fn(f"\n爬取完成，共 {len(all_results)} 个关键词")
    return all_results
