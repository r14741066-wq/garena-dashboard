"""
main.py — Garena 台灣玩家聲音儀表板進入點

用法：
  python3 main.py --scrape              # 立即抓取 + 分析 + 寫入 Notion
  python3 main.py --report              # 立即產生週報 + 寄送 Gmail
  python3 main.py --dry-run             # 抓取 + 分析，只印出結果（不寫 Notion，不寄信）

  # Notion 初始化（首次使用執行一次）
  python3 main.py --init-notion --page-id <PAGE_ID>

  # 查詢
  python3 main.py --stats               # 顯示統計
  python3 main.py --search Bug回報      # 搜尋 Notion 資料庫
  python3 main.py --search 卡頓 --limit 10
"""
import argparse
import logging
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from config import Config, ConfigError, load_config, load_notion_config

# ── 日誌設定 ────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("dashboard.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("main")


# ── 主要流程 ────────────────────────────────────────────────


def _alert(config: Config, step: str, error: str) -> None:
    """寄錯誤通知信（失敗不拋例外）。"""
    try:
        from email_sender import send_error_alert
        send_error_alert(step, error, config)
    except Exception as e:
        logger.error(f"無法寄出錯誤通知：{e}")


def run_scrape(config: Config, dry_run: bool = False) -> bool:
    """
    完整執行抓取 → 去重 → 過濾 → Claude 分析 → 寫 Notion 流程。
    dry_run=True 時只印出結果，不寫 Notion 也不記錄 seen。
    """
    from scrapers.appstore import AppStoreScraper
    from scrapers.google_play import GooglePlayScraper
    from scrapers.ptt import PTTScraper
    from scrapers.dcard import DcardScraper
    from processing.deduplicator import Deduplicator
    from processing.filter import filter_items
    from analyzer import analyze_items
    from notion_db import init_notion, upsert_item

    logger.info("=" * 60)
    logger.info("Garena 玩家聲音儀表板 — 開始抓取")
    logger.info("=" * 60)

    since = datetime.utcnow() - timedelta(days=config.scrape_lookback_days)
    logger.info(f"抓取範圍：{since.date()} 至今（{config.scrape_lookback_days} 天）")

    # 0. 驗證 Notion 連線（dry_run 跳過）
    if not dry_run:
        try:
            init_notion(config.notion_token, config.notion_items_db_id, config.notion_reports_db_id)
        except RuntimeError as e:
            logger.error(f"Notion 連線失敗，中止執行：{e}")
            _alert(config, "Notion 連線失敗", str(e))
            return False

    # 1. 抓取
    logger.info("步驟 1/4：從各平台抓取玩家回饋…")
    scrapers = [
        AppStoreScraper(max_items=config.max_items_per_source),
        GooglePlayScraper(max_items=config.max_items_per_source),
        PTTScraper(max_items=config.max_items_per_source),
        DcardScraper(max_items=config.max_items_per_source),
    ]
    all_raw = []
    for scraper in scrapers:
        items = scraper.fetch(since)
        all_raw.extend(items)
    logger.info(f"共抓取 {len(all_raw)} 筆原始資料")

    if not all_raw:
        logger.warning("未抓到任何資料，本次跳過。")
        _alert(config, "爬蟲全部回傳 0 筆", "四個平台（App Store、Google Play、PTT、Dcard）均未抓到資料，可能是網路問題或平台封鎖。")
        return False

    # 2. 去重 + 過濾
    logger.info("步驟 2/4：去重與過濾…")
    dedup = Deduplicator()
    new_items = dedup.filter_new(all_raw)
    clean_items = filter_items(new_items)
    logger.info(f"去重後 {len(new_items)} 筆，過濾後 {len(clean_items)} 筆")

    if not clean_items:
        logger.info("無新內容需要分析，本次結束。")
        dedup.log_run("scrape", len(all_raw), 0, 0, True)
        return True

    # 3. Claude 分析
    logger.info(f"步驟 3/4：送出 {len(clean_items)} 筆給 Claude 分析…")
    from analyzer import analyze_items
    analyses = analyze_items(clean_items, config)
    logger.info(f"分析完成：{len(analyses)} 筆")

    # 4. 輸出
    if dry_run:
        logger.info("步驟 4/4：Dry-run 模式，印出分析結果（不寫 Notion）")
        _print_analyses(analyses)
        return True

    logger.info(f"步驟 4/4：寫入 Notion（{len(analyses)} 筆）…")
    written = 0
    for analysis in analyses:
        try:
            upsert_item(analysis, config.notion_token, config.notion_items_db_id)
            written += 1
        except Exception as e:
            logger.warning(f"Notion 寫入失敗：{e}")

    # 只在成功寫入後標記 seen
    dedup.mark_seen(clean_items)
    dedup.save_analyses(analyses)
    dedup.log_run("scrape", len(all_raw), len(clean_items), len(analyses), True)

    logger.info(f"完成。Notion 寫入 {written}/{len(analyses)} 筆。")

    # 自動重新生成 HTML 儀表板（不開啟瀏覽器，供 launchd 排程使用）
    try:
        from dashboard_generator import build_dashboard
        build_dashboard(open_browser=False)
    except Exception as e:
        logger.warning(f"儀表板生成失敗（不影響主流程）：{e}")
        _alert(config, "儀表板生成失敗", str(e))

    return True


def run_report(config: Config) -> bool:
    """從 state.db 取本週資料，產生週報並寄送 Gmail + 存 Notion。"""
    from processing.deduplicator import Deduplicator
    from aggregator import build_weekly_report
    from notion_db import init_notion, save_weekly_report
    from email_sender import send_weekly_digest

    logger.info("=" * 60)
    logger.info("Garena 玩家聲音儀表板 — 開始產生週報")
    logger.info("=" * 60)

    try:
        init_notion(config.notion_token, config.notion_items_db_id, config.notion_reports_db_id)
    except RuntimeError as e:
        logger.error(f"Notion 連線失敗：{e}")
        return False

    dedup = Deduplicator()
    analyses = dedup.get_recent_analyses(days=config.report_lookback_days)
    baselines = dedup.get_source_baselines(weeks=4)

    if not analyses:
        logger.warning("本週無分析資料，無法產生週報。")
        return False

    logger.info(f"取得 {len(analyses)} 筆本週資料，開始彙整…")
    report = build_weekly_report(analyses, baselines)

    # 存 Notion
    try:
        save_weekly_report(report, config.notion_token, config.notion_reports_db_id)
    except Exception as e:
        logger.warning(f"週報存 Notion 失敗（不影響寄信）：{e}")

    # 產生各遊戲 AI 洞察（供週報使用），同時取得與儀表板一致的總則數
    game_summaries = {}
    dashboard_total = None
    try:
        from dashboard_generator import generate_ai_summary, load_data
        all_data = load_data(config.report_lookback_days)
        dashboard_total = len(all_data)
        data_aov = [d for d in all_data if d.get("game") == "aov"]
        data_ff  = [d for d in all_data if d.get("game") == "freefire"]
        logger.info("正在產生各遊戲 AI 洞察摘要…")
        game_summaries["aov"] = generate_ai_summary(data_aov, "傳說對決")
        game_summaries["ff"]  = generate_ai_summary(data_ff,  "Free Fire")
        logger.info("AI 洞察摘要完成")
    except Exception as e:
        logger.warning(f"AI 摘要產生失敗（不影響寄信）：{e}")

    # 寄信
    success = send_weekly_digest(report, config,
                                 game_summaries=game_summaries,
                                 total_display=dashboard_total)
    if not success:
        _alert(config, "週報寄送失敗", "send_weekly_digest 回傳 False，請確認 Gmail 設定是否正常。")
    dedup.log_run("report", 0, 0, len(analyses), success)
    return success


def _print_analyses(analyses) -> None:
    print("\n" + "=" * 60)
    print(f"分析結果（Dry-run）｜共 {len(analyses)} 則")
    print("=" * 60)
    for a in analyses[:20]:  # 最多印 20 則
        kw = "、".join(a.keywords) if a.keywords else "—"
        print(f"\n[{a.sentiment}｜{a.category}] {a.summary or a.body[:60]}")
        print(f"  來源：{a.source}  關鍵字：{kw}")
        print(f"  {a.url}")
    if len(analyses) > 20:
        print(f"\n  …（共 {len(analyses)} 則，只顯示前 20 則）")
    print("=" * 60)


# ── Notion 初始化 ────────────────────────────────────────────


def run_init_notion(page_id: str) -> None:
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).parent / ".env", override=True)

    token = os.getenv("NOTION_TOKEN", "").strip()
    if not token or token.startswith("secret_your"):
        print(
            "\n錯誤：請先在 .env 填入 NOTION_TOKEN\n"
            "  前往 notion.so/my-integrations 建立 Integration 取得 Token\n",
            file=sys.stderr,
        )
        sys.exit(1)

    print(f"\n正在 Notion 頁面 {page_id[:8]}… 建立資料庫…")
    from notion_db import create_notion_databases
    try:
        items_db_id, reports_db_id = create_notion_databases(token, page_id)
    except RuntimeError as e:
        print(f"\n失敗：{e}", file=sys.stderr)
        sys.exit(1)

    # 寫回 .env
    env_path = Path(__file__).parent / ".env"
    _write_env_value(env_path, "NOTION_ITEMS_DB_ID", items_db_id)
    _write_env_value(env_path, "NOTION_REPORTS_DB_ID", reports_db_id)

    print(f"\n✅ 資料庫建立成功！")
    print(f"   玩家回饋分析 DB：{items_db_id}")
    print(f"   週報紀錄 DB：    {reports_db_id}")
    print(f"   已自動寫入 .env")
    print(f"\n現在可以執行：python3 main.py --dry-run\n")


def _write_env_value(env_path: Path, key: str, value: str) -> None:
    if env_path.exists():
        lines = env_path.read_text(encoding="utf-8").splitlines()
        new_lines, replaced = [], False
        for line in lines:
            if line.startswith(f"{key}=") or line.startswith(f"{key} ="):
                new_lines.append(f"{key}={value}")
                replaced = True
            else:
                new_lines.append(line)
        if not replaced:
            new_lines.append(f"{key}={value}")
        env_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    else:
        env_path.write_text(f"{key}={value}\n", encoding="utf-8")


# ── 查詢指令 ────────────────────────────────────────────────


def run_search(keyword: str, limit: int) -> None:
    try:
        token, items_db_id, _ = load_notion_config()
        from notion_db import search_items, print_search_results
        results = search_items(keyword, token, items_db_id, limit)
        print_search_results(results, keyword)
    except ConfigError as e:
        print(f"\n設定錯誤：{e}\n", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"搜尋失敗：{e}", file=sys.stderr)
        sys.exit(1)


def run_stats() -> None:
    try:
        token, items_db_id, _ = load_notion_config()
        from notion_db import get_stats
        stats = get_stats(token, items_db_id)
        print(f"\nNotion 資料庫統計")
        print(f"{'─' * 40}")
        print(f"總回饋數：{stats['total']} 筆")
        print(f"\n類別分布：")
        for cat, count in sorted(stats["by_category"].items(), key=lambda x: -x[1]):
            bar = "█" * min(count, 30)
            print(f"  {cat:6s}  {bar} {count}")
        print(f"\n情緒分布：")
        for sent, count in stats["by_sentiment"].items():
            print(f"  {sent}：{count} 則")
        print()
    except ConfigError as e:
        print(f"\n設定錯誤：{e}\n", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"統計失敗：{e}", file=sys.stderr)


# ── CLI ─────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Garena 台灣玩家聲音儀表板",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
範例：
  # 首次設定（只需執行一次）
  python3 main.py --init-notion --page-id <PAGE_ID>

  # 日常使用
  python3 main.py --dry-run          # 測試：抓取 + 分析，印出結果
  python3 main.py --scrape           # 完整抓取 + 分析 + 寫 Notion
  python3 main.py --report           # 產生週報 + 寄送 Gmail

  # Notion 查詢
  python3 main.py --search Bug回報
  python3 main.py --search 卡頓 --limit 10
  python3 main.py --stats
        """,
    )
    parser.add_argument("--scrape",      action="store_true", help="抓取 + 分析 + 寫 Notion + 更新儀表板")
    parser.add_argument("--report",      action="store_true", help="產生週報 + 寄送 Gmail")
    parser.add_argument("--dry-run",     action="store_true", help="抓取 + 分析，只印出（不寫 Notion，不寄信）")
    parser.add_argument("--dashboard",   action="store_true", help="立即重新生成並開啟 HTML 儀表板")
    parser.add_argument("--init-notion", action="store_true", help="建立 Notion 資料庫（需搭配 --page-id）")
    parser.add_argument("--page-id",     metavar="PAGE_ID",   help="Notion 父頁面 ID（用於 --init-notion）")
    parser.add_argument("--search",      metavar="關鍵字",     help="搜尋 Notion 資料庫")
    parser.add_argument("--limit",       type=int, default=20, help="搜尋結果最大筆數（預設 20）")
    parser.add_argument("--stats",       action="store_true", help="顯示 Notion 資料庫統計")
    args = parser.parse_args()

    # --init-notion：不需要完整設定
    if args.init_notion:
        if not args.page_id:
            print("\n錯誤：--init-notion 需要搭配 --page-id <PAGE_ID>\n", file=sys.stderr)
            parser.print_help()
            sys.exit(1)
        run_init_notion(args.page_id)
        return

    # --dashboard：不需要任何憑證
    if args.dashboard:
        from dashboard_generator import build_dashboard
        build_dashboard(open_browser=True)
        return

    # --search / --stats：只需要 Notion 憑證
    if args.search:
        run_search(args.search, args.limit)
        return
    if args.stats:
        run_stats()
        return

    # 其他模式需要完整設定
    try:
        config = load_config()
    except ConfigError as e:
        print(f"\n設定錯誤：\n{e}\n", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        run_scrape(config, dry_run=True)
    elif args.scrape:
        try:
            success = run_scrape(config)
        except Exception as e:
            logger.exception("--scrape 發生未預期錯誤")
            _alert(config, "未預期錯誤（--scrape）", str(e))
            sys.exit(1)
        sys.exit(0 if success else 1)
    elif args.report:
        try:
            success = run_report(config)
        except Exception as e:
            logger.exception("--report 發生未預期錯誤")
            _alert(config, "未預期錯誤（--report）", str(e))
            sys.exit(1)
        sys.exit(0 if success else 1)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
