"""
analyzer.py — 使用 Claude API 對每則玩家回饋進行結構化分析
輸出：類別、情緒、關鍵字（1-3個）、一句話摘要
"""
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import anthropic

from config import Config
from scrapers.base import RawItem

logger = logging.getLogger(__name__)

CATEGORIES = ["客服問題", "活動反應", "Bug回報", "付費體驗", "遊戲體驗", "其他"]
SENTIMENTS = ["正面", "負面", "中性"]

SYSTEM_PROMPT = """你是 Garena 台灣玩家體驗分析師，負責分類與分析玩家在各平台留下的遊戲評論與討論文章。
請用繁體中文回應，輸出嚴格符合指定的 JSON 格式，不要輸出任何 JSON 以外的文字。"""

USER_PROMPT_TEMPLATE = """以下是 {count} 則玩家回饋，請逐一分析每則內容。

{items_block}

請輸出以下 JSON 格式（只輸出 JSON array，不要其他文字）：

```json
[
  {{
    "index": 1,
    "category": "遊戲體驗",
    "sentiment": "負面",
    "keywords": ["卡頓", "當機"],
    "summary": "玩家反映遊戲在低階手機上頻繁當機，嚴重影響遊戲體驗。"
  }}
]
```

欄位規則：

- category：從以下選項中選一個最符合的
  可選值：{categories}
  - 客服問題：帳號封鎖、申訴、客服回覆品質
  - 活動反應：對活動設計、活動獎勵的反應
  - Bug回報：遊戲錯誤、閃退、功能異常
  - 付費體驗：儲值問題、道具定價、付費機制
  - 遊戲體驗：操作感、平衡性、玩法、畫質、網路
  - 其他：以上皆不符合

- sentiment：整體語氣
  - 正面：讚美、感謝、滿意
  - 負面：抱怨、批評、憤怒
  - 中性：純粹描述、提問、無明顯傾向

- keywords：1-3 個最能代表此則回饋的關鍵詞（繁體中文，盡量具體）

- summary：一句話（20-50字）概述此則回饋的核心內容，聚焦在「玩家說了什麼」
"""


@dataclass
class ItemAnalysis:
    native_id: str
    source: str
    title: str
    body: str
    url: str
    rating: Optional[int]
    published_at: datetime
    category: str
    sentiment: str
    keywords: list[str] = field(default_factory=list)
    summary: str = ""


class AnalyzerError(Exception):
    pass


def analyze_items(items: list[RawItem], config: Config) -> list[ItemAnalysis]:
    """批次分析所有 items，每批最多 batch_size 則。"""
    if not items:
        return []

    client = anthropic.Anthropic(api_key=config.anthropic_api_key)
    batch_size = config.batch_size_claude
    all_analyses: list[ItemAnalysis] = []

    for i in range(0, len(items), batch_size):
        batch = items[i:i + batch_size]
        logger.info(f"分析批次 {i // batch_size + 1}：{len(batch)} 則（共 {len(items)} 則）")
        try:
            analyses = _analyze_batch(batch, client, config.claude_model)
            all_analyses.extend(analyses)
        except AnalyzerError as e:
            logger.warning(f"批次分析失敗，改用逐一分析：{e}")
            for item in batch:
                try:
                    single = _analyze_single(item, client, config.claude_model)
                    all_analyses.append(single)
                except Exception as e2:
                    logger.warning(f"單則分析失敗，使用備用值：{e2}")
                    all_analyses.append(_fallback_analysis(item))

        if i + batch_size < len(items):
            time.sleep(1)  # 批次間稍作停頓

    logger.info(f"分析完成：共 {len(all_analyses)} 則")
    return all_analyses


def _analyze_batch(batch: list[RawItem], client: anthropic.Anthropic, model: str) -> list[ItemAnalysis]:
    items_block = _build_items_block(batch)
    prompt = USER_PROMPT_TEMPLATE.format(
        count=len(batch),
        items_block=items_block,
        categories="、".join(CATEGORIES),
    )

    response_text = _call_with_retry(client, model, prompt)
    return _parse_response(response_text, batch)


def _analyze_single(item: RawItem, client: anthropic.Anthropic, model: str) -> ItemAnalysis:
    """單則分析（批次失敗時的 fallback）。"""
    items_block = _build_items_block([item])
    prompt = USER_PROMPT_TEMPLATE.format(
        count=1,
        items_block=items_block,
        categories="、".join(CATEGORIES),
    )
    response_text = _call_with_retry(client, model, prompt)
    analyses = _parse_response(response_text, [item])
    return analyses[0] if analyses else _fallback_analysis(item)


def _build_items_block(items: list[RawItem]) -> str:
    lines = []
    for i, item in enumerate(items, 1):
        pub_str = item.published_at.strftime("%Y-%m-%d") if item.published_at else ""
        body_preview = item.body[:800].strip()
        lines.append(f"[{i}] 來源：{item.source}  |  日期：{pub_str}")
        if item.title:
            lines.append(f"    標題：{item.title}")
        if item.rating:
            lines.append(f"    評分：{item.rating}/5")
        lines.append(f"    內容：{body_preview}")
        lines.append("")
    return "\n".join(lines)


def _call_with_retry(client: anthropic.Anthropic, model: str, prompt: str) -> str:
    """呼叫 Claude API，失敗時以指數退避重試（複用 AI新聞 模式）。"""
    delays = [5, 10, 20]
    last_error = None

    for attempt, delay in enumerate(delays + [None], 1):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=4096,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.content[0].text
        except anthropic.RateLimitError as e:
            last_error = e
            logger.warning(f"Claude API 速率限制（第 {attempt} 次），{delay}s 後重試")
        except anthropic.APIStatusError as e:
            if e.status_code and e.status_code >= 500:
                last_error = e
                logger.warning(f"Claude API 伺服器錯誤 {e.status_code}（第 {attempt} 次），{delay}s 後重試")
            else:
                raise AnalyzerError(f"Claude API 錯誤（{e.status_code}）：{e.message}") from e
        except anthropic.APIConnectionError as e:
            last_error = e
            logger.warning(f"Claude API 連線失敗（第 {attempt} 次），{delay}s 後重試")

        if delay is not None:
            time.sleep(delay)

    raise AnalyzerError(f"Claude API 重試 {len(delays)} 次後仍失敗：{last_error}") from last_error


def _parse_response(response_text: str, items: list[RawItem]) -> list[ItemAnalysis]:
    """解析 Claude 回應的 JSON array，對應回原始 items。"""
    text = response_text.strip()

    # 找出 JSON array（可能被 ```json ... ``` 包住）
    if "```" in text:
        start = text.find("[", text.find("```"))
        end = text.rfind("]") + 1
    else:
        start = text.find("[")
        end = text.rfind("]") + 1

    if start == -1 or end == 0:
        logger.warning("Claude 回應中找不到 JSON array，使用備用結果")
        return [_fallback_analysis(item) for item in items]

    try:
        data = json.loads(text[start:end])
    except json.JSONDecodeError as e:
        logger.warning(f"JSON 解析失敗：{e}，使用備用結果")
        return [_fallback_analysis(item) for item in items]

    item_map = {i + 1: item for i, item in enumerate(items)}
    analyses = []

    for entry in data:
        idx = entry.get("index", 0)
        item = item_map.get(idx)
        if not item:
            continue

        category = entry.get("category", "其他")
        if category not in CATEGORIES:
            category = "其他"

        sentiment = entry.get("sentiment", "中性")
        if sentiment not in SENTIMENTS:
            sentiment = "中性"

        keywords = entry.get("keywords", [])
        if isinstance(keywords, list):
            keywords = [str(k) for k in keywords[:3]]
        else:
            keywords = []

        analyses.append(ItemAnalysis(
            native_id=item.native_id,
            source=item.source,
            title=item.title,
            body=item.body,
            url=item.url,
            rating=item.rating,
            published_at=item.published_at,
            category=category,
            sentiment=sentiment,
            keywords=keywords,
            summary=entry.get("summary", ""),
        ))

    if not analyses:
        return [_fallback_analysis(item) for item in items]

    return analyses


def _fallback_analysis(item: RawItem) -> ItemAnalysis:
    """Claude 無法分析時的備用：填入預設值。"""
    return ItemAnalysis(
        native_id=item.native_id,
        source=item.source,
        title=item.title,
        body=item.body,
        url=item.url,
        rating=item.rating,
        published_at=item.published_at,
        category="其他",
        sentiment="中性",
        keywords=[],
        summary="（分析失敗）",
    )
