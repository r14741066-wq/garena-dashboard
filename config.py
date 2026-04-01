"""
config.py — 讀取 .env 設定並驗證必填欄位
所有模組皆從此處取得設定，啟動時即驗證，避免排程觸發後才報錯。
"""
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env", override=True)


class ConfigError(Exception):
    pass


@dataclass
class Config:
    # Claude
    anthropic_api_key: str
    claude_model: str = "claude-sonnet-4-6"

    # Gmail SMTP
    gmail_sender: str = ""
    gmail_app_password: str = ""
    gmail_recipient: str = ""

    # Notion
    notion_token: str = ""
    notion_items_db_id: str = ""
    notion_reports_db_id: str = ""

    # 行為設定
    scrape_lookback_days: int = 2   # 每次抓取往回看幾天（日常排程用 1-2 天即可）
    report_lookback_days: int = 7   # 週報 / 儀表板彙整幾天的資料
    max_items_per_source: int = 50
    batch_size_claude: int = 20


def load_config() -> Config:
    """讀取並驗證環境變數，缺少必填欄位時拋出 ConfigError 並說明原因。"""
    required = {
        "ANTHROPIC_API_KEY":   "Anthropic API 金鑰（從 console.anthropic.com 取得）",
        "GMAIL_SENDER":        "寄件人 Gmail 地址",
        "GMAIL_APP_PASSWORD":  "Gmail App Password（myaccount.google.com/apppasswords）",
        "GMAIL_RECIPIENT":     "收件人電子郵件地址",
        "NOTION_TOKEN":        "Notion Integration Token（notion.so/my-integrations → secret_xxx）",
        "NOTION_ITEMS_DB_ID":  "玩家回饋分析 Notion DB ID（執行 --init-notion 自動建立）",
        "NOTION_REPORTS_DB_ID": "週報紀錄 Notion DB ID（執行 --init-notion 自動建立）",
    }

    missing = []
    for key, desc in required.items():
        val = os.getenv(key, "").strip()
        if not val or val.startswith("your") or val.startswith("sk-ant-...") or val.startswith("secret_your"):
            missing.append(f"  {key}  →  {desc}")

    if missing:
        raise ConfigError(
            "缺少必要的設定，請編輯 .env 檔案填入以下欄位：\n"
            + "\n".join(missing)
            + "\n\n參考 .env.example 了解設定格式。"
        )

    def get_int(key: str, default: int) -> int:
        try:
            return int(os.getenv(key, str(default)))
        except ValueError:
            return default

    return Config(
        anthropic_api_key=os.environ["ANTHROPIC_API_KEY"].strip(),
        gmail_sender=os.environ["GMAIL_SENDER"].strip(),
        gmail_app_password=os.environ["GMAIL_APP_PASSWORD"].strip(),
        gmail_recipient=os.environ["GMAIL_RECIPIENT"].strip(),
        notion_token=os.environ["NOTION_TOKEN"].strip(),
        notion_items_db_id=os.environ["NOTION_ITEMS_DB_ID"].strip(),
        notion_reports_db_id=os.environ["NOTION_REPORTS_DB_ID"].strip(),
        scrape_lookback_days=get_int("SCRAPE_LOOKBACK_DAYS", 2),
        report_lookback_days=get_int("REPORT_LOOKBACK_DAYS", 7),
        max_items_per_source=get_int("MAX_ITEMS_PER_SOURCE", 50),
        batch_size_claude=get_int("BATCH_SIZE_CLAUDE", 20),
    )


def load_notion_config() -> tuple[str, str, str]:
    """
    僅讀取 Notion 憑證，不驗證其他欄位。
    用於 --search / --stats / --init-notion 等不需要完整設定的指令。
    """
    token = os.getenv("NOTION_TOKEN", "").strip()
    items_db_id = os.getenv("NOTION_ITEMS_DB_ID", "").strip()
    reports_db_id = os.getenv("NOTION_REPORTS_DB_ID", "").strip()

    missing = []
    if not token or token.startswith("secret_your"):
        missing.append("  NOTION_TOKEN  →  Notion Integration Token（notion.so/my-integrations）")
    if not items_db_id:
        missing.append("  NOTION_ITEMS_DB_ID  →  執行 python3 main.py --init-notion --page-id <ID> 自動建立")
    if not reports_db_id:
        missing.append("  NOTION_REPORTS_DB_ID  →  執行 python3 main.py --init-notion --page-id <ID> 自動建立")

    if missing:
        raise ConfigError("缺少 Notion 設定：\n" + "\n".join(missing))

    return token, items_db_id, reports_db_id
