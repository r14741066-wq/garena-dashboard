"""
scrapers/base.py — 爬蟲基礎類別與共用資料結構
"""
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class RawItem:
    """從各來源抓取的原始資料。"""
    source: str           # "appstore_freefire" | "appstore_aov" | "googleplay_freefire" | "googleplay_aov" | "ptt" | "dcard"
    native_id: str        # 平台原生唯一 ID
    title: str            # 討論串標題（App Store/Google Play 評論填空字串）
    body: str             # 全文內容
    rating: Optional[int] # App Store/Google Play 1-5 分，論壇為 None
    author: str           # 作者名稱
    url: str              # 原文連結
    published_at: datetime  # 原始發布時間


class BaseScraper(ABC):
    """所有爬蟲的抽象基底類別。"""

    def fetch(self, since: datetime) -> list[RawItem]:
        """
        安全地抓取資料，單一來源失敗不影響整體流程。
        子類別實作 _fetch()，此方法負責 try/except 包裝。
        """
        try:
            items = self._fetch(since)
            logger.info(f"[{self.__class__.__name__}] 抓取到 {len(items)} 筆")
            return items
        except Exception as e:
            logger.warning(f"[{self.__class__.__name__}] 抓取失敗，跳過：{e}")
            return []

    @abstractmethod
    def _fetch(self, since: datetime) -> list[RawItem]:
        """子類別實作此方法，拋出例外時由 fetch() 捕捉。"""
        ...
