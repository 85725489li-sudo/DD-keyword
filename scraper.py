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


def get_internal_id_from_page(page, app_store_id: str, log) -> str | None:
    """从搜索页 HTML 提取内部 ID"""
    try:
        page.goto(
            f"https://app.diandian.com/search?keyword={app_store_id}&type=app",
            timeout=20000, wait_until="domcontentloaded",
        )
        page.wait_for_timeout(2000)
        content = page.content()
        matches = re.findall(r'/app/([a-z0-9]{10,})/ios', content)
        counter = Counter(matches)
        candidates = [(iid, cnt) for iid, cnt in counter.most_common() if cnt <= 4]
        iid = candidates[0][0] if candidates else (counter.most_common(1)[0][0] if counter else None)
        if iid:
            log(f"  内部 ID: {iid}")
            save_internal_id_cache(app_store_id, iid)
            return iid
    except Exception as e:
        log(f"  搜索失败: {e}")
    return None


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

    # 获取 iTunes App 信息（无需浏览器）
    log_fn(f"获取 {len(app_ids)} 个 App 信息...")
    app_info_map = {}
    for app_id in app_ids:
        info = get_app_info_itunes(app_id, country_code)
        app_info_map[app_id] = info
        log_fn(f"  {info.get('name') or app_id} ({app_id})")

    all_results = []
    cookies = get_cookies()

    # 启动一个浏览器，复用处理所有 App（避免反复启动的开销）
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=bool(IS_SERVER),
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu",
                  "--no-blink-features=AutomationControlled"],
        )
        context = browser.new_context(
            viewport={"width": 1440, "height": 900},
            locale="zh-CN",
            timezone_id="Asia/Shanghai",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
        )

        if cookies:
            try:
                context.add_cookies(cookies)
                log_fn("已加载 Session Cookie")
            except Exception:
                pass

        page = context.new_page()

        for i, app_id in enumerate(app_ids):
            log_fn(f"\n[{i+1}/{len(app_ids)}] 处理: {app_info_map[app_id].get('name', app_id)}")

            # 获取内部 ID（优先缓存）
            internal_id = get_internal_id_cache(app_id)
            if internal_id:
                log_fn(f"  内部 ID（缓存）: {internal_id}")
            else:
                internal_id = get_internal_id_from_page(page, app_id, log_fn)

            if not internal_id:
                log_fn(f"  ⚠️ 跳过")
                continue

            # 拦截关键词 API
            captured = {}

            def on_response(response):
                url = response.url
                if "word/analysi/detail" in url and response.status == 200:
                    try:
                        body = response.json()
                        if body.get("code") == 0 and body.get("data"):
                            captured["data"] = body["data"]
                            log_fn("  ★ 捕获关键词数据")
                    except Exception:
                        pass

            page.on("response", on_response)

            # 直接带 section=keyword 导航，省去点 tab
            aso_url = f"https://app.diandian.com/app/{internal_id}/ios-aso?market=1&country={country_id}&section=keyword"
            log_fn(f"  加载关键词页面...")
            page.goto(aso_url, timeout=30000, wait_until="domcontentloaded")

            # 最多等 25 秒
            for _ in range(25):
                if "data" in captured:
                    break
                page.wait_for_timeout(1000)

            page.remove_listener("response", on_response)

            if "data" not in captured:
                log_fn("  ⚠️ 未获取到数据")
                keywords = []
            else:
                raw = captured["data"]
                items = raw if isinstance(raw, list) else raw.get("list", raw.get("data", []))
                keywords = _filter_keywords(items, filters)

            for kw in keywords:
                kw["app_id"] = app_id
                kw["app_name"] = app_info_map[app_id].get("name", app_id)
                kw["app_icon"] = app_info_map[app_id].get("icon", "")

            all_results.extend(keywords)
            log_fn(f"  ✅ {len(keywords)} 个关键词")

        context.close()
        browser.close()

    log_fn(f"\n爬取完成，共 {len(all_results)} 个关键词")
    return all_results
