import sqlite3
import json
import os
from datetime import datetime

DB_PATH = os.environ.get("DB_PATH", "dd_keywords.db")


def get_db():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS jobs (
            id TEXT PRIMARY KEY,
            status TEXT DEFAULT 'pending',
            app_ids TEXT NOT NULL,
            country_id TEXT NOT NULL,
            country_code TEXT NOT NULL,
            country_name TEXT NOT NULL,
            rank_max INTEGER DEFAULT 10,
            popularity_min INTEGER DEFAULT 0,
            search_index_min INTEGER DEFAULT 0,
            search_results_min INTEGER DEFAULT 0,
            created_at TEXT NOT NULL,
            started_at TEXT,
            finished_at TEXT,
            total_keywords INTEGER DEFAULT 0,
            error TEXT
        );

        CREATE TABLE IF NOT EXISTS results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            app_id TEXT NOT NULL,
            app_name TEXT,
            app_icon TEXT,
            keyword TEXT NOT NULL,
            rank INTEGER,
            popularity INTEGER,
            search_index INTEGER,
            search_results INTEGER,
            FOREIGN KEY (job_id) REFERENCES jobs(id)
        );

        CREATE TABLE IF NOT EXISTS job_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT NOT NULL,
            message TEXT NOT NULL,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS session (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            cookies TEXT,
            updated_at TEXT
        );

        INSERT OR IGNORE INTO session (id, cookies, updated_at) VALUES (1, NULL, NULL);

        CREATE TABLE IF NOT EXISTS app_id_cache (
            app_store_id TEXT PRIMARY KEY,
            internal_id TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
    """)
    conn.commit()
    conn.close()


def create_job(job_id, req):
    conn = get_db()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT INTO jobs (id, app_ids, country_id, country_code, country_name,
            rank_max, popularity_min, search_index_min, search_results_min, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        job_id,
        json.dumps(req["app_ids"]),
        req["country_id"],
        req["country_code"],
        req["country_name"],
        req.get("rank_max", 10),
        req.get("popularity_min", 0),
        req.get("search_index_min", 0),
        req.get("search_results_min", 0),
        now,
    ))
    conn.commit()
    conn.close()


def update_job(job_id, **kwargs):
    conn = get_db()
    fields = ", ".join(f"{k} = ?" for k in kwargs)
    values = list(kwargs.values()) + [job_id]
    conn.execute(f"UPDATE jobs SET {fields} WHERE id = ?", values)
    conn.commit()
    conn.close()


def get_job(job_id):
    conn = get_db()
    row = conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def list_jobs(limit=50):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM jobs ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def save_results(job_id, results):
    conn = get_db()
    conn.executemany("""
        INSERT INTO results (job_id, app_id, app_name, app_icon, keyword,
            rank, popularity, search_index, search_results)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        (job_id, r["app_id"], r["app_name"], r["app_icon"], r["keyword"],
         r["rank"], r["popularity"], r["search_index"], r["search_results"])
        for r in results
    ])
    conn.commit()
    conn.close()


def get_results(job_id):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM results WHERE job_id = ? ORDER BY popularity DESC",
        (job_id,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def add_log(job_id, message):
    conn = get_db()
    conn.execute(
        "INSERT INTO job_logs (job_id, message, created_at) VALUES (?, ?, ?)",
        (job_id, message, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_logs(job_id, after_id=0):
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM job_logs WHERE job_id = ? AND id > ? ORDER BY id ASC",
        (job_id, after_id)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_internal_id_cache(app_store_id):
    conn = get_db()
    row = conn.execute(
        "SELECT internal_id FROM app_id_cache WHERE app_store_id = ?", (app_store_id,)
    ).fetchone()
    conn.close()
    return row["internal_id"] if row else None


def save_internal_id_cache(app_store_id, internal_id):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO app_id_cache (app_store_id, internal_id, updated_at) VALUES (?, ?, ?)",
        (app_store_id, internal_id, datetime.now().isoformat())
    )
    conn.commit()
    conn.close()


def get_cookies():
    conn = get_db()
    row = conn.execute("SELECT cookies FROM session WHERE id = 1").fetchone()
    conn.close()
    if row and row["cookies"]:
        return json.loads(row["cookies"])
    return None


def save_cookies(cookies):
    conn = get_db()
    conn.execute(
        "UPDATE session SET cookies = ?, updated_at = ? WHERE id = 1",
        (json.dumps(cookies), datetime.now().isoformat())
    )
    conn.commit()
    conn.close()
