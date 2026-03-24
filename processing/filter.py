"""
processing/filter.py — 過濾廣告、無意義、太短的內容
"""
import re
import logging

from scrapers.base import RawItem

logger = logging.getLogger(__name__)

MIN_BODY_LENGTH = 10
CJK_RATIO_THRESHOLD = 0.15  # 至少 15% 是中文/日文/韓文字符

AD_PATTERNS = [
    r"加line.*優惠",
    r"私訊.*領取",
    r"點擊.*連結.*領",
    r"官方客服.*line",
    r"免費.*鑽石.*領取",
    r"hack|cheat|mod apk",
    r"\+\d{5,}",            # 電話號碼類
    r"http[s]?://bit\.ly",  # 短網址廣告
]

_AD_RE = re.compile("|".join(AD_PATTERNS), re.IGNORECASE)
_CJK_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff\uac00-\ud7af]")


def filter_items(items: list[RawItem]) -> list[RawItem]:
    """過濾掉廣告、太短、非中文的 items，回傳乾淨的列表。"""
    filtered = [item for item in items if _is_meaningful(item)]
    removed = len(items) - len(filtered)
    if removed:
        logger.info(f"[Filter] 過濾掉 {removed} 筆無意義/廣告內容，剩餘 {len(filtered)} 筆")
    return filtered


def _is_meaningful(item: RawItem) -> bool:
    body = item.body.strip()

    if len(body) < MIN_BODY_LENGTH:
        return False

    if _AD_RE.search(body):
        return False

    if not _has_enough_cjk(body):
        return False

    return True


def _has_enough_cjk(text: str) -> bool:
    if not text:
        return False
    cjk_count = len(_CJK_RE.findall(text))
    return (cjk_count / len(text)) >= CJK_RATIO_THRESHOLD
