import os
import json
import time
import random
import requests
from playwright.sync_api import sync_playwright
from database import get_cookies, save_cookies, add_log

DD_EMAIL = os.environ.get("DD_EMAIL", "")
DD_PASSWORD = os.environ.get("DD_PASSWORD", "")
IS_SERVER = os.environ.get("RENDER", "") or os.environ.get("HEADLESS", "")

ITUNES_API = "https://itunes.apple.com/lookup"


def human_delay(min_s=0.8, max_s=2.0):
    time.sleep(random.uniform(min_s, max_s))


def get_app_info_itunes(app_id: str, country_code: str) -> dict:
    """从 iTunes API 获取 App 名称和图标"""
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


def login_diandian(page, log):
    """使用邮箱/密码登录点点数据"""
    if not DD_EMAIL or not DD_PASSWORD:
        log("⚠️ 未配置 DD_EMAIL / DD_PASSWORD，跳过自动登录")
        return False

    log(f"正在登录点点数据账号...")
    try:
        page.goto("https://app.diandian.com", timeout=30000, wait_until="domcontentloaded")
        human_delay(2, 3)

        # 点击登录按钮
        for sel in ["text=登录", "text=登录/注册", ".login-btn", "[href*='login']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    human_delay(1, 2)
                    break
            except Exception:
                pass

        # 尝试点击邮箱登录 tab
        for sel in ["text=邮箱登录", "text=账号密码", "text=密码登录"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    human_delay(0.5, 1)
                    break
            except Exception:
                pass

        # 输入邮箱
        for sel in ["input[type='email']", "input[placeholder*='邮箱']", "input[name='email']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.fill(DD_EMAIL)
                    human_delay(0.5, 1)
                    break
            except Exception:
                pass

        # 输入密码
        for sel in ["input[type='password']", "input[placeholder*='密码']", "input[name='password']"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.fill(DD_PASSWORD)
                    human_delay(0.5, 1)
                    break
            except Exception:
                pass

        # 点击提交
        for sel in ["button[type='submit']", "text=登录", ".submit-btn"]:
            try:
                el = page.locator(sel).first
                if el.is_visible(timeout=2000):
                    el.click()
                    break
            except Exception:
                pass

        page.wait_for_timeout(4000)

        # 检查是否登录成功
        current_url = page.url
        if "login" not in current_url and "diandian.com" in current_url:
            cookies = page.context.cookies()
            save_cookies(cookies)
            log("✅ 登录成功，已保存 Session")
            return True
        else:
            log("❌ 自动登录失败，请手动更新 Session Cookie")
            return False

    except Exception as e:
        log(f"❌ 登录异常: {e}")
        return False


def get_internal_id(page, app_store_id: str, log) -> str | None:
    """通过搜索获取点点数据内部 App ID"""
    import re
    from collections import Counter

    log(f"  搜索内部 ID: {app_store_id}")
    page.goto(
        f"https://app.diandian.com/search?keyword={app_store_id}&type=app",
        timeout=30000,
        wait_until="domcontentloaded",
    )
    human_delay(3, 5)

    # 从页面 HTML 中提取内部 ID
    content = page.content()
    matches = re.findall(r'/app/([a-z0-9]{10,})/ios', content)
    counter = Counter(matches)

    # 出现次数少的更可能是搜索结果（非导航固定项）
    candidates = [(iid, cnt) for iid, cnt in counter.most_common() if cnt <= 4]
    if candidates:
        iid = candidates[0][0]
        log(f"  内部 ID: {iid}")
        return iid

    if counter:
        iid = counter.most_common(1)[0][0]
        log(f"  内部 ID (fallback): {iid}")
        return iid

    log(f"  ⚠️ 未找到内部 ID")
    return None


def fetch_keywords(page, internal_id: str, country_id: str, filters: dict, log) -> list:
    """抓取 ASO 关键词数据"""
    captured = {}

    def on_response(response):
        url = response.url
        if "api.diandian.com" in url and response.status == 200:
            try:
                ct = response.headers.get("content-type", "")
                if "json" in ct:
                    body = response.json()
                    if body.get("code") == 0 and body.get("data"):
                        captured[url] = body["data"]
                        if "word/analysi/detail" in url:
                            log("  ★ 捕获关键词数据")
            except Exception:
                pass

    page.on("response", on_response)

    aso_url = f"https://app.diandian.com/app/{internal_id}/ios-aso?market=1&country={country_id}"
    page.goto(aso_url, timeout=30000, wait_until="domcontentloaded")
    page.wait_for_timeout(3000)

    # 点击关键词 tab
    for sel in ["text=关键词", "li:has-text('关键词')", ".tab:has-text('关键词')"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                human_delay(1, 2)
                break
        except Exception:
            pass

    # 强制刷新触发 API
    page.keyboard.press("Control+F5")
    page.wait_for_timeout(5000)

    # 再次点击关键词 tab
    for sel in ["text=关键词", "li:has-text('关键词')"]:
        try:
            el = page.locator(sel).first
            if el.is_visible(timeout=2000):
                el.click()
                human_delay(1, 2)
                break
        except Exception:
            pass

    # 滚动触发懒加载
    for _ in range(20):
        page.mouse.wheel(0, 300)
        page.wait_for_timeout(400)

    page.wait_for_timeout(6000)

    # 等待 detail API
    detail_url = next((u for u in captured if "word/analysi/detail" in u), None)
    if not detail_url:
        log("  再等待 10 秒...")
        page.wait_for_timeout(10000)
        detail_url = next((u for u in captured if "word/analysi/detail" in u), None)

    page.remove_listener("response", on_response)

    if not detail_url:
        log("  ⚠️ 未捕获到关键词数据")
        return []

    raw = captured[detail_url]
    items = raw if isinstance(raw, list) else raw.get("list", raw.get("data", []))
    log(f"  共 {len(items)} 个关键词，开始筛选...")

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
    log(f"  筛选后: {len(results)} 个关键词")
    return results


def run_scrape_job(job: dict, log_fn):
    """主入口：执行一个爬取任务"""
    app_ids = json.loads(job["app_ids"])
    country_id = job["country_id"]
    country_code = job["country_code"]
    filters = {
        "rank_max": job.get("rank_max", 10),
        "popularity_min": job.get("popularity_min", 0),
        "search_index_min": job.get("search_index_min", 0),
        "search_results_min": job.get("search_results_min", 0),
    }

    all_results = []

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

        # 加载 Cookie
        cookies = get_cookies()
        if cookies:
            try:
                context.add_cookies(cookies)
                log_fn("已加载 Session Cookie")
            except Exception:
                pass

        page = context.new_page()

        # 验证登录状态
        page.goto("https://app.diandian.com", timeout=30000, wait_until="domcontentloaded")
        human_delay(2, 3)
        try:
            logged_in = not page.locator("text=登录/注册").is_visible(timeout=3000)
        except Exception:
            logged_in = True

        if not logged_in:
            log_fn("Session 已过期，尝试自动登录...")
            login_diandian(page, log_fn)

        # 获取 iTunes App 信息
        log_fn(f"获取 {len(app_ids)} 个 App 信息...")
        app_info_map = {}
        for app_id in app_ids:
            info = get_app_info_itunes(app_id, country_code)
            app_info_map[app_id] = info
            name = info.get("name") or app_id
            log_fn(f"  App: {name} ({app_id})")

        # 逐个爬取
        for i, app_id in enumerate(app_ids):
            log_fn(f"\n[{i+1}/{len(app_ids)}] 处理 App: {app_id}")
            app_info = app_info_map[app_id]

            internal_id = get_internal_id(page, app_id, log_fn)
            if not internal_id:
                log_fn(f"  跳过 {app_id}")
                continue

            keywords = fetch_keywords(page, internal_id, country_id, filters, log_fn)

            for kw in keywords:
                kw["app_id"] = app_id
                kw["app_name"] = app_info.get("name", app_id)
                kw["app_icon"] = app_info.get("icon", "")

            all_results.extend(keywords)
            log_fn(f"  ✅ {app_id} 完成，{len(keywords)} 个关键词")

            if i < len(app_ids) - 1:
                human_delay(3, 5)

        context.close()
        browser.close()

    log_fn(f"\n爬取完成，共 {len(all_results)} 个关键词")
    return all_results
