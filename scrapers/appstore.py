"""
scrapers/appstore.py — 抓取 App Store 台灣區評論
使用 Apple 官方 RSS Feed（JSON 格式），穩定且不依賴第三方套件。
Feed URL: https://itunes.apple.com/tw/rss/customerreviews/id={app_id}/sortBy=mostRecent/json
每頁最多 50 筆，共 10 頁（500 筆上限）。
"""
import logging
import time
from datetime import datetime, timezone

import httpx

from scrapers.base import BaseScraper, RawItem

logger = logging.getLogger(__name__)

APPS = {
    "appstore_freefire": {
        "app_id":  "1175305978",
        "display": "Free Fire（App Store）",
    },
    "appstore_aov": {
        "app_id":  "1038378089",
        "display": "傳說對決（App Store）",
    },
}

RSS_URL = "https://itunes.apple.com/tw/rss/customerreviews/page={page}/id={app_id}/sortBy=mostRecent/json"
MAX_PAGES = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
    "Accept": "application/json",
    "Accept-Language": "zh-TW,zh;q=0.9",
}


class AppStoreScraper(BaseScraper):
    def __init__(self, max_items: int = 50):
        self.max_items = max_items

    def _fetch(self, since: datetime) -> list[RawItem]:
        all_items: list[RawItem] = []
        for source_key, app_info in APPS.items():
            try:
                items = self._fetch_app(source_key, app_info, since)
                all_items.extend(items)
            except Exception as e:
                logger.warning(f"[AppStore] {app_info['display']} 抓取失敗：{e}")
            time.sleep(1.5)
        return all_items

    def _fetch_app(self, source_key: str, app_info: dict, since: datetime) -> list[RawItem]:
        since_naive = since.replace(tzinfo=None) if since.tzinfo else since
        items = []

        with httpx.Client(headers=HEADERS, timeout=20, follow_redirects=True) as client:
            for page in range(1, MAX_PAGES + 1):
                url = RSS_URL.format(page=page, app_id=app_info["app_id"])
                try:
                    resp = client.get(url)
                    resp.raise_for_status()
                    data = resp.json()
                except Exception as e:
                    logger.warning(f"[AppStore] 第 {page} 頁請求失敗：{e}")
                    break

                entries = data.get("feed", {}).get("entry", [])
                # 第一筆 entry 是 App 資訊本身，跳過
                if page == 1 and entries:
                    entries = entries[1:]

                if not entries:
                    break  # 沒有更多評論

                reached_before_since = False
                for entry in entries:
                    try:
                        # 日期
                        updated_str = entry.get("updated", {}).get("label", "")
                        pub_dt = _parse_rss_date(updated_str)
                        pub_dt_naive = pub_dt.replace(tzinfo=None) if (pub_dt and pub_dt.tzinfo) else pub_dt

                        if pub_dt_naive and pub_dt_naive < since_naive:
                            reached_before_since = True
                            continue

                        # 內容
                        body = entry.get("content", {}).get("label", "").strip()
                        if not body:
                            body = entry.get("summary", {}).get("label", "").strip()
                        if not body:
                            continue

                        # ID
                        native_id = entry.get("id", {}).get("label", "").split("?")[0].split("/")[-1]
                        if not native_id:
                            continue

                        # 評分
                        rating_str = entry.get("im:rating", {}).get("label", "")
                        rating = int(rating_str) if rating_str.isdigit() else None

                        # 作者
                        author = entry.get("author", {}).get("name", {}).get("label", "")

                        # 標題
                        title = entry.get("title", {}).get("label", "").strip()

                        items.append(RawItem(
                            source=source_key,
                            native_id=native_id,
                            title=title,
                            body=body,
                            rating=rating,
                            author=author,
                            url=f"https://apps.apple.com/tw/app/id{app_info['app_id']}",
                            published_at=pub_dt_naive or datetime.utcnow(),
                        ))

                        if len(items) >= self.max_items:
                            logger.info(f"[AppStore] {app_info['display']}：{len(items)} 筆（達上限）")
                            return items

                    except Exception as e:
                        logger.debug(f"[AppStore] 解析單筆失敗：{e}")
                        continue

                if reached_before_since:
                    break

                time.sleep(0.8)

        logger.info(f"[AppStore] {app_info['display']}：{len(items)} 筆新評論（since {since_naive.date()}）")
        return items


def _parse_rss_date(date_str: str):
    """解析 Apple RSS 回傳的 ISO 8601 日期，如 '2026-03-22T10:30:00-07:00'"""
    if not date_str:
        return None
    try:
        # Python 3.7+ 支援帶時區的 ISO 格式
        return datetime.fromisoformat(date_str)
    except Exception:
        try:
            # fallback：截取前 19 碼
            return datetime.fromisoformat(date_str[:19])
        except Exception:
            return None
