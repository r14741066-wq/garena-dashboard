"""
scrapers/google_play.py — 抓取 Google Play 台灣區 Free Fire 與傳說對決評論
使用 google-play-scraper 套件（pip install google-play-scraper）
"""
import logging
import time
from datetime import datetime

from google_play_scraper import Sort, reviews

from scrapers.base import BaseScraper, RawItem

logger = logging.getLogger(__name__)

APPS = {
    "googleplay_freefire": {
        "app_id":  "com.dts.freefireth",
        "lang":    "zh",
        "country": "tw",
        "display": "Free Fire（Google Play）",
    },
    "googleplay_aov": {
        "app_id":  "com.garena.game.kgth",
        "lang":    "zh",
        "country": "tw",
        "display": "傳說對決（Google Play）",
    },
}

FETCH_COUNT = 200  # 每次最多抓取 200 筆（Google Play 實際上限）


class GooglePlayScraper(BaseScraper):
    def __init__(self, max_items: int = 50):
        self.max_items = max_items

    def _fetch(self, since: datetime) -> list[RawItem]:
        all_items: list[RawItem] = []
        for source_key, app_info in APPS.items():
            try:
                items = self._fetch_app(source_key, app_info, since)
                all_items.extend(items)
            except Exception as e:
                logger.warning(f"[GooglePlay] {app_info['display']} 抓取失敗：{e}")
            time.sleep(2)
        return all_items

    def _fetch_app(self, source_key: str, app_info: dict, since: datetime) -> list[RawItem]:
        since_naive = since.replace(tzinfo=None) if since.tzinfo else since

        result, _ = reviews(
            app_info["app_id"],
            lang=app_info["lang"],
            country=app_info["country"],
            sort=Sort.NEWEST,
            count=FETCH_COUNT,
        )

        items = []
        for r in result:
            try:
                published_at = r.get("at")
                if published_at is None:
                    continue
                if hasattr(published_at, "tzinfo") and published_at.tzinfo:
                    published_at = published_at.replace(tzinfo=None)

                if published_at < since_naive:
                    continue

                native_id = r.get("reviewId", "")
                if not native_id:
                    continue

                body = r.get("content", "").strip()
                if not body:
                    continue

                items.append(RawItem(
                    source=source_key,
                    native_id=native_id,
                    title="",
                    body=body,
                    rating=r.get("score"),
                    author=r.get("userName", ""),
                    url=f"https://play.google.com/store/apps/details?id={app_info['app_id']}",
                    published_at=published_at,
                ))

                if len(items) >= self.max_items:
                    break
            except Exception as e:
                logger.debug(f"[GooglePlay] 解析單筆評論失敗：{e}")
                continue

        logger.info(f"[GooglePlay] {app_info['display']}：{len(items)} 筆新評論")
        return items
