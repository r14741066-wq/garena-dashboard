"""
notion_db.py — Notion 資料庫整合
兩個資料庫：
  1. 玩家回饋分析（Items DB）— 每則回饋的分析結果
  2. 週報紀錄（Reports DB）— 每週彙整報告

公開函式：
  init_notion(token, items_db_id, reports_db_id)     — 驗證連線
  create_notion_databases(token, page_id)             — 一鍵建立兩個 DB，回傳 (items_db_id, reports_db_id)
  upsert_item(analysis, token, db_id)                 — 新增/更新單筆回饋分析
  save_weekly_report(report, token, db_id)            — 儲存週報
  get_stats(token, db_id)                             — 統計資料
  search_items(keyword, token, db_id)                 — 關鍵字搜尋
"""
import json
import logging
import time
from datetime import datetime
from typing import Optional

from notion_client import Client
from notion_client.errors import APIResponseError

from analyzer import ItemAnalysis
from aggregator import WeeklyReport

logger = logging.getLogger(__name__)

CATEGORIES = ["客服問題", "活動反應", "Bug回報", "付費體驗", "遊戲體驗", "其他"]
SENTIMENTS = ["正面", "負面", "中性"]
SOURCES = [
    "appstore_freefire", "appstore_aov",
    "googleplay_freefire", "googleplay_aov",
    "ptt", "dcard",
]
GAMES = ["Free Fire", "傳說對決", "不確定"]

ITEMS_DB_SCHEMA = {
    "摘要": {"title": {}},
    "來源": {
        "select": {
            "options": [{"name": s} for s in SOURCES]
        }
    },
    "遊戲": {
        "select": {
            "options": [{"name": g} for g in GAMES]
        }
    },
    "類別": {
        "select": {
            "options": [
                {"name": "客服問題", "color": "orange"},
                {"name": "活動反應", "color": "blue"},
                {"name": "Bug回報",  "color": "red"},
                {"name": "付費體驗", "color": "yellow"},
                {"name": "遊戲體驗", "color": "green"},
                {"name": "其他",     "color": "gray"},
            ]
        }
    },
    "情緒": {
        "select": {
            "options": [
                {"name": "正面", "color": "green"},
                {"name": "負面", "color": "red"},
                {"name": "中性", "color": "gray"},
            ]
        }
    },
    "關鍵字": {"multi_select": {}},
    "評分": {"number": {"format": "number"}},
    "原文連結": {"url": {}},
    "作者": {"rich_text": {}},
    "發布日期": {"date": {}},
    "收錄日期": {"date": {}},
    "原文內容": {"rich_text": {}},
    "Native ID": {"rich_text": {}},
}

REPORTS_DB_SCHEMA = {
    "週報標題": {"title": {}},
    "週期開始": {"date": {}},
    "週期結束": {"date": {}},
    "總回饋數": {"number": {"format": "number"}},
    "負面比例": {"number": {"format": "percent"}},
    "客服問題數": {"number": {"format": "number"}},
    "Bug回報數": {"number": {"format": "number"}},
    "活動反應數": {"number": {"format": "number"}},
    "付費體驗數": {"number": {"format": "number"}},
    "遊戲體驗數": {"number": {"format": "number"}},
    "熱門關鍵字": {"rich_text": {}},
    "主要負面議題": {"rich_text": {}},
    "量體異常來源": {"rich_text": {}},
    "生成時間": {"date": {}},
}


# ── 公開函式 ────────────────────────────────────────────────


def init_notion(token: str, items_db_id: str, reports_db_id: str) -> None:
    """驗證 Notion token 和兩個 database_id 是否可存取。"""
    client = Client(auth=token)
    for db_id, name in [(items_db_id, "玩家回饋分析"), (reports_db_id, "週報紀錄")]:
        try:
            client.databases.retrieve(database_id=db_id)
            logger.info(f"Notion [{name}] 連線成功（ID: {db_id[:8]}…）")
        except APIResponseError as e:
            raise RuntimeError(
                f"Notion [{name}] 資料庫連線失敗：{e.code} — {str(e)}\n"
                "請確認：\n"
                "  1. NOTION_TOKEN 是否正確（secret_xxx）\n"
                "  2. NOTION_ITEMS_DB_ID / NOTION_REPORTS_DB_ID 是否正確\n"
                "  3. 資料庫已分享給你的 Integration（頁面右上角 … → Connect to）"
            ) from e


def create_notion_databases(token: str, page_id: str) -> tuple[str, str]:
    """
    在指定 Notion 頁面下建立兩個資料庫。
    回傳 (items_db_id, reports_db_id)。
    """
    client = Client(auth=token)

    items_db_id = _create_database(client, page_id, "玩家回饋分析", ITEMS_DB_SCHEMA)
    reports_db_id = _create_database(client, page_id, "週報紀錄", REPORTS_DB_SCHEMA)

    return items_db_id, reports_db_id


def upsert_item(analysis: ItemAnalysis, token: str, db_id: str) -> None:
    """新增或更新單筆回饋分析（以 native_id + source 去重）。"""
    client = Client(auth=token)
    today = datetime.utcnow().strftime("%Y-%m-%d")

    try:
        existing_id = _find_page_by_native_id(client, db_id, analysis.native_id, analysis.source)
        props = _build_item_properties(analysis, today)

        if existing_id:
            client.pages.update(page_id=existing_id, properties=props)
        else:
            client.pages.create(
                parent={"database_id": db_id},
                properties=props,
            )
        time.sleep(0.35)  # Notion API ~3 req/s
    except APIResponseError as e:
        logger.warning(f"Notion 寫入失敗 [{analysis.native_id}]: {e.code} — {str(e)}")
    except Exception as e:
        logger.warning(f"Notion 寫入失敗 [{analysis.native_id}]: {e}")


def save_weekly_report(report: WeeklyReport, token: str, db_id: str) -> None:
    """儲存週報到 Notion 週報紀錄 DB。"""
    client = Client(auth=token)
    props = _build_report_properties(report)
    try:
        client.pages.create(
            parent={"database_id": db_id},
            properties=props,
        )
        logger.info(f"週報已存入 Notion：{report.week_start} - {report.week_end}")
    except APIResponseError as e:
        logger.warning(f"Notion 週報寫入失敗：{e.code} — {str(e)}")


def get_stats(token: str, db_id: str) -> dict:
    """統計 Items DB 中的資料分布。"""
    client = Client(auth=token)
    all_pages = []

    try:
        cursor = None
        while True:
            kwargs = {"database_id": db_id, "page_size": 100}
            if cursor:
                kwargs["start_cursor"] = cursor
            response = client.databases.query(**kwargs)
            all_pages.extend(response.get("results", []))
            if not response.get("has_more"):
                break
            cursor = response.get("next_cursor")
    except APIResponseError as e:
        logger.error(f"Notion stats 查詢失敗：{e.code} — {str(e)}")
        return {"total": 0, "by_category": {}, "by_sentiment": {}}

    by_category: dict[str, int] = {c: 0 for c in CATEGORIES}
    by_sentiment: dict[str, int] = {s: 0 for s in SENTIMENTS}

    for page in all_pages:
        props = page.get("properties", {})
        cat = _get_select(props, "類別")
        if cat in by_category:
            by_category[cat] += 1
        sent = _get_select(props, "情緒")
        if sent in by_sentiment:
            by_sentiment[sent] += 1

    return {
        "total": len(all_pages),
        "by_category": by_category,
        "by_sentiment": by_sentiment,
    }


def search_items(keyword: str, token: str, db_id: str, limit: int = 20) -> list[dict]:
    """關鍵字搜尋 Notion Items DB。"""
    client = Client(auth=token)

    if keyword in CATEGORIES:
        notion_filter = {"property": "類別", "select": {"equals": keyword}}
    elif keyword in SENTIMENTS:
        notion_filter = {"property": "情緒", "select": {"equals": keyword}}
    else:
        notion_filter = {"property": "摘要", "title": {"contains": keyword}}

    try:
        response = client.databases.query(
            database_id=db_id,
            filter=notion_filter,
            page_size=min(limit, 100),
            sorts=[{"property": "收錄日期", "direction": "descending"}],
        )
        return [_page_to_dict(p) for p in response.get("results", [])]
    except APIResponseError as e:
        logger.error(f"Notion 搜尋失敗：{e.code} — {str(e)}")
        return []


def print_search_results(results: list[dict], keyword: str) -> None:
    if not results:
        print(f"\n找不到包含「{keyword}」的回饋。")
        return
    print(f"\n搜尋「{keyword}」— 找到 {len(results)} 筆結果\n" + "─" * 60)
    for i, r in enumerate(results, 1):
        print(f"\n[{i}] {r.get('summary', '─')}")
        print(f"    來源：{r.get('source', '─')}  |  情緒：{r.get('sentiment', '─')}  |  類別：{r.get('category', '─')}")
        print(f"    關鍵字：{'、'.join(r.get('keywords', [])) or '─'}")
        print(f"    {r.get('url', '─')}")
    print()


# ── 私有函式 ────────────────────────────────────────────────


def _create_database(client: Client, page_id: str, title: str, schema: dict) -> str:
    try:
        response = client.databases.create(
            parent={"type": "page_id", "page_id": page_id},
            title=[{"type": "text", "text": {"content": title}}],
            properties=schema,
        )
        db_id = response["id"]
        logger.info(f"Notion [{title}] 建立成功：{db_id}")
        return db_id
    except APIResponseError as e:
        raise RuntimeError(
            f"建立 Notion 資料庫「{title}」失敗：{e.code} — {str(e)}\n"
            "請確認頁面 ID 正確且已分享給 Integration。"
        ) from e


def _find_page_by_native_id(
    client: Client, db_id: str, native_id: str, source: str
) -> Optional[str]:
    try:
        response = client.databases.query(
            database_id=db_id,
            filter={"property": "Native ID", "rich_text": {"equals": f"{source}__{native_id}"}},
            page_size=1,
        )
        results = response.get("results", [])
        return results[0]["id"] if results else None
    except APIResponseError:
        return None


def _infer_game(source: str) -> str:
    if "freefire" in source:
        return "Free Fire"
    if "aov" in source:
        return "傳說對決"
    return "不確定"


def _build_item_properties(analysis: ItemAnalysis, today: str) -> dict:
    summary = analysis.summary or analysis.title or analysis.body[:50]
    props: dict = {
        "摘要": {"title": [{"text": {"content": summary[:2000]}}]},
        "來源": {"select": {"name": analysis.source}},
        "遊戲": {"select": {"name": _infer_game(analysis.source)}},
        "類別": {"select": {"name": analysis.category}},
        "情緒": {"select": {"name": analysis.sentiment}},
        "收錄日期": {"date": {"start": today}},
        "Native ID": {"rich_text": [{"text": {"content": f"{analysis.source}__{analysis.native_id}"}}]},
    }

    if analysis.keywords:
        props["關鍵字"] = {"multi_select": [{"name": k[:100]} for k in analysis.keywords]}

    if analysis.rating is not None:
        props["評分"] = {"number": analysis.rating}

    if analysis.url:
        props["原文連結"] = {"url": analysis.url}

    if analysis.published_at:
        props["發布日期"] = {"date": {"start": analysis.published_at.strftime("%Y-%m-%d")}}

    if analysis.body:
        props["原文內容"] = {"rich_text": [{"text": {"content": analysis.body[:2000]}}]}

    return props


def _build_report_properties(report: WeeklyReport) -> dict:
    total = report.total_items
    negative = report.by_sentiment.get("負面", 0)
    negative_pct = (negative / total) if total > 0 else 0

    top_kw_str = "、".join(f"{k}({n})" for k, n in report.top_keywords[:10])
    neg_issues = "\n".join(
        f"• {c['category']}（{c['count']}則）：{c['sample']}"
        for c in report.negative_concentration
    )
    anomaly_str = "、".join(report.anomaly_sources) if report.anomaly_sources else "無"

    title = f"{report.week_start.strftime('%Y/%m/%d')} - {report.week_end.strftime('%Y/%m/%d')} 週報"

    return {
        "週報標題": {"title": [{"text": {"content": title}}]},
        "週期開始": {"date": {"start": report.week_start.isoformat()}},
        "週期結束": {"date": {"start": report.week_end.isoformat()}},
        "總回饋數": {"number": total},
        "負面比例": {"number": round(negative_pct, 4)},
        "客服問題數": {"number": report.by_category.get("客服問題", 0)},
        "Bug回報數": {"number": report.by_category.get("Bug回報", 0)},
        "活動反應數": {"number": report.by_category.get("活動反應", 0)},
        "付費體驗數": {"number": report.by_category.get("付費體驗", 0)},
        "遊戲體驗數": {"number": report.by_category.get("遊戲體驗", 0)},
        "熱門關鍵字": {"rich_text": [{"text": {"content": top_kw_str[:2000]}}]},
        "主要負面議題": {"rich_text": [{"text": {"content": neg_issues[:2000]}}]},
        "量體異常來源": {"rich_text": [{"text": {"content": anomaly_str}}]},
        "生成時間": {"date": {"start": report.generated_at.strftime("%Y-%m-%d")}},
    }


def _page_to_dict(page: dict) -> dict:
    props = page.get("properties", {})
    return {
        "summary":  _get_title(props, "摘要"),
        "source":   _get_select(props, "來源"),
        "category": _get_select(props, "類別"),
        "sentiment": _get_select(props, "情緒"),
        "keywords": _get_multi_select(props, "關鍵字"),
        "url":      _get_url(props, "原文連結"),
        "published": _get_date(props, "發布日期"),
    }


# ── Notion 屬性 helpers ────────────────────────────────────


def _get_title(props: dict, key: str) -> str:
    try:
        return props[key]["title"][0]["plain_text"]
    except (KeyError, IndexError):
        return ""


def _get_rich_text(props: dict, key: str) -> str:
    try:
        return props[key]["rich_text"][0]["plain_text"]
    except (KeyError, IndexError):
        return ""


def _get_select(props: dict, key: str) -> str:
    try:
        return props[key]["select"]["name"] or ""
    except (KeyError, TypeError):
        return ""


def _get_multi_select(props: dict, key: str) -> list[str]:
    try:
        return [opt["name"] for opt in props[key]["multi_select"]]
    except (KeyError, TypeError):
        return []


def _get_date(props: dict, key: str) -> str:
    try:
        return props[key]["date"]["start"] or ""
    except (KeyError, TypeError):
        return ""


def _get_url(props: dict, key: str) -> str:
    try:
        return props[key]["url"] or ""
    except (KeyError, TypeError):
        return ""
