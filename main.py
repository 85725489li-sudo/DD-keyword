import os
import json
import uuid
import asyncio
import csv
import io
import threading
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from database import (
    init_db, create_job, update_job, get_job, list_jobs,
    save_results, get_results, get_logs, add_log, save_cookies,
)
from scraper import run_scrape_job

app = FastAPI(title="DD Keyword Tool")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局任务队列（线程安全）
job_queue: asyncio.Queue = None
executor = ThreadPoolExecutor(max_workers=1)  # 同时只运行 1 个爬取任务


class JobRequest(BaseModel):
    app_ids: list[str]
    country_id: str
    country_code: str
    country_name: str
    popularity_min: int = 0
    search_index_min: int = 0
    search_results_min: int = 0


class CookiesUpdate(BaseModel):
    cookies: list[dict]


@app.on_event("startup")
async def startup():
    init_db()

    # 从环境变量加载 Cookie
    env_cookies = os.environ.get("DD_COOKIES", "")
    if env_cookies:
        try:
            cookies = json.loads(env_cookies)
            save_cookies(cookies)
            print("已从环境变量加载 DD_COOKIES")
        except Exception as e:
            print(f"DD_COOKIES 解析失败: {e}")

    global job_queue
    job_queue = asyncio.Queue()
    asyncio.create_task(job_worker())


# ─────────────────────────────────────────
# 任务队列 Worker
# ─────────────────────────────────────────

async def job_worker():
    """后台 Worker，逐个处理爬取任务"""
    while True:
        job_id = await job_queue.get()
        job = get_job(job_id)
        if not job:
            continue

        def log(msg):
            add_log(job_id, msg)

        try:
            update_job(job_id, status="running", started_at=datetime.now().isoformat())
            log("任务开始...")

            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                executor,
                lambda: run_scrape_job(job, log)
            )

            save_results(job_id, results)
            update_job(
                job_id,
                status="done",
                finished_at=datetime.now().isoformat(),
                total_keywords=len(results),
            )
            log(f"__DONE__{len(results)}")

        except Exception as e:
            update_job(job_id, status="failed", error=str(e))
            log(f"__ERROR__{str(e)}")

        finally:
            job_queue.task_done()


# ─────────────────────────────────────────
# API 路由
# ─────────────────────────────────────────

@app.post("/api/jobs")
async def create_job_endpoint(req: JobRequest):
    # 过滤空的 app_ids
    app_ids = [a.strip() for a in req.app_ids if a.strip()]
    if not app_ids:
        raise HTTPException(400, "至少需要一个 App ID")

    job_id = str(uuid.uuid4())
    create_job(job_id, {
        "app_ids": app_ids,
        "country_id": req.country_id,
        "country_code": req.country_code,
        "country_name": req.country_name,
        "popularity_min": req.popularity_min,
        "search_index_min": req.search_index_min,
        "search_results_min": req.search_results_min,
    })

    await job_queue.put(job_id)
    queue_pos = job_queue.qsize()

    return {"job_id": job_id, "queue_position": queue_pos}


@app.get("/api/jobs/{job_id}")
async def get_job_endpoint(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@app.get("/api/jobs/{job_id}/stream")
async def stream_job(job_id: str):
    """SSE 实时推送任务日志"""

    async def generate():
        last_id = 0
        consecutive_empty = 0

        # 发送心跳保持连接
        yield "data: {\"type\":\"connected\"}\n\n"

        while True:
            job = get_job(job_id)
            if not job:
                yield f"data: {{\"type\":\"error\",\"message\":\"任务不存在\"}}\n\n"
                break

            logs = get_logs(job_id, after_id=last_id)
            for log_row in logs:
                msg = log_row["message"]
                last_id = log_row["id"]
                consecutive_empty = 0

                if msg.startswith("__DONE__"):
                    total = msg.replace("__DONE__", "")
                    yield f"data: {{\"type\":\"done\",\"total\":{total}}}\n\n"
                    return
                elif msg.startswith("__ERROR__"):
                    error = msg.replace("__ERROR__", "")
                    yield f"data: {{\"type\":\"error\",\"message\":\"{error}\"}}\n\n"
                    return
                else:
                    yield f"data: {{\"type\":\"log\",\"message\":{json.dumps(msg)}}}\n\n"

            if job["status"] in ("done", "failed") and not logs:
                consecutive_empty += 1
                if consecutive_empty >= 3:
                    status = "done" if job["status"] == "done" else "error"
                    total = job.get("total_keywords", 0)
                    yield f"data: {{\"type\":\"{status}\",\"total\":{total}}}\n\n"
                    break

            await asyncio.sleep(1)

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/jobs/{job_id}/results")
async def get_results_endpoint(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return get_results(job_id)


@app.get("/api/history")
async def get_history():
    jobs = list_jobs(100)
    for job in jobs:
        job["app_ids"] = json.loads(job["app_ids"])
    return jobs


@app.get("/api/export/{job_id}")
async def export_csv(job_id: str):
    job = get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")

    results = get_results(job_id)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["App ID", "App名称", "关键词", "排名", "流行度", "搜索指数", "搜索结果数"])
    for r in results:
        writer.writerow([
            r["app_id"], r["app_name"], r["keyword"],
            r["rank"], r["popularity"], r["search_index"], r["search_results"],
        ])

    output.seek(0)
    filename = f"keywords_{job_id[:8]}.csv"
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@app.get("/api/queue-status")
async def queue_status():
    return {"pending": job_queue.qsize() if job_queue else 0}


@app.post("/api/admin/cookies")
async def update_cookies(req: CookiesUpdate):
    """管理员接口：手动更新 Session Cookie"""
    save_cookies(req.cookies)
    return {"message": "Cookie 已更新"}


# ─────────────────────────────────────────
# 静态文件
# ─────────────────────────────────────────

app.mount("/", StaticFiles(directory="static", html=True), name="static")
