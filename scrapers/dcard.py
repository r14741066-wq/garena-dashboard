"""
scrapers/dcard.py — Dcard 已停用

Dcard 使用 Cloudflare Managed Challenge，無法在自動化腳本中穩定存取。
此模組保留作為未來擴充用途，目前直接回傳空列表。
"""
import logging
from datetime import datetime
from scrapers.base import BaseScraper, RawItem

logger = logging.getLogger(__name__)


class DcardScraper(BaseScraper):
    def __init__(self, max_items: int = 50):
        self.max_items = max_items

    def _fetch(self, since: datetime) -> list[RawItem]:
        logger.debug("[Dcard] 已停用，跳過")
        return []
