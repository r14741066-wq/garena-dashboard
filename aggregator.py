"""
aggregator.py — 每週彙整：統計、Top 10 關鍵詞、負評叢集、量體異常偵測
從 state.db 的 analyses 表讀取本週資料並計算。
"""
import logging
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

CATEGORIES = ["客服問題", "活動反應", "Bug回報", "付費體驗", "遊戲體驗", "其他"]
SENTIMENTS = ["正面", "負面", "中性"]


@dataclass
class WeeklyReport:
    week_start: date
    week_end: date
    total_items: int
    by_category: dict[str, int]
    by_sentiment: dict[str, int]
    top_keywords: list[tuple[str, int]]        # [(詞, 次數), ...]
    negative_concentration: list[dict]         # [{"category": ..., "count": ..., "samples": [...]}]
    activity_voices: list[dict]                # 活動反應類別的正負聲音
    anomaly_sources: list[str]                 # 本週量體 > 2x 均值的來源
    generated_at: datetime


def build_weekly_report(
    analyses: list[dict],
    baselines: Optional[dict[str, float]] = None,
) -> WeeklyReport:
    """
    從 analyses（來自 state.db 的 dict 列表）建立週報。
    baselines: {source: avg_items_per_week}，用於量體異常偵測。
    """
    now = datetime.utcnow()
    week_end = now.date()
    week_start = week_end - timedelta(days=6)

    total = len(analyses)

    # 各類別統計
    by_category: dict[str, int] = {c: 0 for c in CATEGORIES}
    for a in analyses:
        cat = a.get("category", "其他")
        if cat in by_category:
            by_category[cat] += 1
        else:
            by_category["其他"] = by_category.get("其他", 0) + 1

    # 情緒統計
    by_sentiment: dict[str, int] = {s: 0 for s in SENTIMENTS}
    for a in analyses:
        sent = a.get("sentiment", "中性")
        if sent in by_sentiment:
            by_sentiment[sent] += 1

    # Top 10 關鍵詞
    top_keywords = _compute_top_keywords(analyses, n=10)

    # 負評最集中的問題
    negative_concentration = _compute_negative_clusters(analyses, top_n=4)

    # 活動反應聲量
    activity_voices = _compute_activity_voices(analyses)

    # 量體異常偵測
    anomaly_sources: list[str] = []
    if baselines:
        source_counts: dict[str, int] = Counter(a.get("source", "") for a in analyses)
        anomaly_sources = _detect_anomalies(source_counts, baselines, threshold=2.0)

    report = WeeklyReport(
        week_start=week_start,
        week_end=week_end,
        total_items=total,
        by_category=by_category,
        by_sentiment=by_sentiment,
        top_keywords=top_keywords,
        negative_concentration=negative_concentration,
        activity_voices=activity_voices,
        anomaly_sources=anomaly_sources,
        generated_at=now,
    )

    logger.info(
        f"週報彙整完成：{total} 則 | "
        f"負面 {by_sentiment.get('負面', 0)} 則 | "
        f"Top keyword: {top_keywords[0][0] if top_keywords else '—'}"
    )
    return report


def _compute_top_keywords(analyses: list[dict], n: int = 10) -> list[tuple[str, int]]:
    """統計所有關鍵詞出現次數，回傳 Top N。"""
    counter: Counter = Counter()
    for a in analyses:
        keywords = a.get("keywords", [])
        if isinstance(keywords, list):
            for kw in keywords:
                if kw:
                    counter[kw] += 1
    return counter.most_common(n)


def _compute_negative_clusters(analyses: list[dict], top_n: int = 4) -> list[dict]:
    """找出負評最集中的類別，每類提供多則引言摘要。"""
    negative = [a for a in analyses if a.get("sentiment") == "負面"]
    if not negative:
        return []

    by_category: dict[str, list[dict]] = {}
    for a in negative:
        cat = a.get("category", "其他")
        by_category.setdefault(cat, []).append(a)

    sorted_cats = sorted(by_category.items(), key=lambda x: -len(x[1]))

    result = []
    for cat, items in sorted_cats[:top_n]:
        # 最多取 3 則不重複的 summary 作為引言
        samples = []
        seen = set()
        for item in items:
            s = item.get("summary") or item.get("body", "")[:80]
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                samples.append(s)
            if len(samples) >= 3:
                break
        result.append({
            "category": cat,
            "count": len(items),
            "samples": samples,
            # 向後相容
            "sample": samples[0] if samples else "",
        })
    return result


def _compute_activity_voices(analyses: list[dict]) -> list[dict]:
    """彙整「活動反應」類別的正負聲音，反映本週行銷活動的玩家反應。"""
    activity = [a for a in analyses if a.get("category") == "活動反應"]
    if not activity:
        return []

    pos_items = [a for a in activity if a.get("sentiment") == "正面"]
    neg_items = [a for a in activity if a.get("sentiment") == "負面"]
    neu_items = [a for a in activity if a.get("sentiment") == "中性"]

    def pick_samples(items, n=2):
        samples = []
        seen = set()
        for item in items:
            s = item.get("summary") or item.get("body", "")[:80]
            s = s.strip()
            if s and s not in seen:
                seen.add(s)
                samples.append(s)
            if len(samples) >= n:
                break
        return samples

    return [{
        "total":    len(activity),
        "pos":      len(pos_items),
        "neg":      len(neg_items),
        "neu":      len(neu_items),
        "pos_samples": pick_samples(pos_items),
        "neg_samples": pick_samples(neg_items),
    }]


def _detect_anomalies(
    current: dict[str, int],
    baselines: dict[str, float],
    threshold: float = 2.0,
) -> list[str]:
    """回傳本週量體超過均值 threshold 倍的來源列表。"""
    anomalies = []
    for source, count in current.items():
        baseline = baselines.get(source, 0)
        if baseline > 0 and count > baseline * threshold:
            anomalies.append(source)
            logger.info(f"量體異常偵測：{source} 本週 {count} 則，均值 {baseline:.1f} 則/週")
    return anomalies
