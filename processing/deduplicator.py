"""
processing/deduplicator.py — 基於 SQLite 的去重與執行歷史記錄
使用 SHA-256 content hash + native_id 雙重去重。
"""
import hashlib
import logging
import sqlite3
from datetime import datetime
from pathlib import Path

from scrapers.base import RawItem

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "state.db"


class Deduplicator:
    def __init__(self, db_path: Path = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS seen_items (
                    id TEXT PRIMARY KEY,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    first_seen TEXT NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS run_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_type TEXT NOT NULL,
                    ran_at TEXT NOT NULL,
                    items_scraped INTEGER DEFAULT 0,
                    items_new INTEGER DEFAULT 0,
                    items_analyzed INTEGER DEFAULT 0,
                    success INTEGER DEFAULT 0
                )
            """)
            # 儲存每筆分析結果，供週報使用
            conn.execute("""
                CREATE TABLE IF NOT EXISTS analyses (
                    native_id TEXT NOT NULL,
                    source TEXT NOT NULL,
                    title TEXT,
                    body TEXT,
                    url TEXT,
                    rating INTEGER,
                    published_at TEXT,
                    category TEXT,
                    sentiment TEXT,
                    keywords TEXT,
                    summary TEXT,
                    analyzed_at TEXT NOT NULL,
                    PRIMARY KEY (native_id, source)
                )
            """)
            conn.commit()

    def filter_new(self, items: list[RawItem]) -> list[RawItem]:
        """過濾掉已見過的 items，回傳只包含新 items 的列表。"""
        new_items = []
        with sqlite3.connect(self.db_path) as conn:
            for item in items:
                item_id = self._item_id(item)
                content_hash = self._content_hash(item)

                row = conn.execute(
                    "SELECT id FROM seen_items WHERE id = ? OR content_hash = ?",
                    (item_id, content_hash)
                ).fetchone()

                if row is None:
                    new_items.append(item)

        logger.info(f"去重後：{len(new_items)}/{len(items)} 筆為新內容")
        return new_items

    def mark_seen(self, items: list[RawItem]) -> None:
        """將 items 標記為已處理，寫入 state.db。"""
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            for item in items:
                conn.execute(
                    "INSERT OR IGNORE INTO seen_items (id, source, content_hash, first_seen) VALUES (?, ?, ?, ?)",
                    (self._item_id(item), item.source, self._content_hash(item), now)
                )
            conn.commit()

    def save_analyses(self, analyses: list) -> None:
        """將 ItemAnalysis 列表儲存到 state.db，供週報彙整使用。"""
        import json
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            for a in analyses:
                conn.execute("""
                    INSERT OR REPLACE INTO analyses
                    (native_id, source, title, body, url, rating, published_at,
                     category, sentiment, keywords, summary, analyzed_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    a.native_id, a.source, a.title, a.body[:2000], a.url,
                    a.rating,
                    a.published_at.isoformat() if a.published_at else None,
                    a.category, a.sentiment,
                    json.dumps(a.keywords, ensure_ascii=False),
                    a.summary, now
                ))
            conn.commit()

    def get_recent_analyses(self, days: int = 7) -> list[dict]:
        """取得最近 N 天的分析結果，供週報使用。"""
        import json
        from datetime import timedelta
        since = (datetime.utcnow() - timedelta(days=days)).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM analyses WHERE analyzed_at >= ? ORDER BY analyzed_at DESC",
                (since,)
            ).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            d["keywords"] = json.loads(d["keywords"]) if d["keywords"] else []
            result.append(d)
        return result

    def get_source_baselines(self, weeks: int = 4) -> dict[str, float]:
        """計算各來源過去 N 週的平均每週數量（供量體異常偵測用）。"""
        from datetime import timedelta
        since = (datetime.utcnow() - timedelta(weeks=weeks)).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT source, COUNT(*) as cnt FROM analyses WHERE analyzed_at >= ? GROUP BY source",
                (since,)
            ).fetchall()

        baselines = {}
        for source, cnt in rows:
            baselines[source] = cnt / weeks
        return baselines

    def log_run(self, run_type: str, items_scraped: int, items_new: int,
                items_analyzed: int, success: bool) -> None:
        """記錄本次執行歷史。"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO run_history (run_type, ran_at, items_scraped, items_new, items_analyzed, success) VALUES (?, ?, ?, ?, ?, ?)",
                (run_type, datetime.utcnow().isoformat(), items_scraped, items_new, items_analyzed, int(success))
            )
            conn.commit()

    @staticmethod
    def _item_id(item: RawItem) -> str:
        return f"{item.source}__{item.native_id}"

    @staticmethod
    def _content_hash(item: RawItem) -> str:
        return hashlib.sha256(item.body.encode("utf-8")).hexdigest()
