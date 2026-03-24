"""
email_sender.py — 將週報彙整渲染成 HTML 電子報並透過 Gmail SMTP 發送
結構：統計摘要框 → Top 10 關鍵詞 → 負評集中議題 → 量體異常警示
"""
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from aggregator import WeeklyReport
from config import Config

logger = logging.getLogger(__name__)

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465

CATEGORY_COLORS = {
    "客服問題": ("#7B341E", "#FEEBC8"),
    "活動反應": ("#2B6CB0", "#BEE3F8"),
    "Bug回報":  ("#742A2A", "#FED7D7"),
    "付費體驗": ("#744210", "#FEFCBF"),
    "遊戲體驗": ("#276749", "#C6F6D5"),
    "其他":     ("#2D3748", "#E2E8F0"),
}
SENTIMENT_COLORS = {
    "正面": ("#276749", "#C6F6D5"),
    "負面": ("#742A2A", "#FED7D7"),
    "中性": ("#2D3748", "#E2E8F0"),
}


class EmailError(Exception):
    pass


def send_weekly_digest(report: WeeklyReport, config: Config) -> bool:
    """發送 HTML 週報，成功回傳 True，失敗記錄錯誤回傳 False。"""
    try:
        # 取得儀表板 URL（若已部署到 GitHub Pages）
        dashboard_url = _get_dashboard_url()

        date_range = f"{report.week_start.strftime('%Y/%m/%d')} - {report.week_end.strftime('%Y/%m/%d')}"
        subject = f"【Garena 玩家聲音】週報 {date_range}"
        html_body = _render_html(report, date_range, dashboard_url)
        plain_body = _render_plain(report, date_range, dashboard_url)
        _send_smtp(subject, html_body, plain_body, config)
        logger.info(f"週報已發送至 {config.gmail_recipient}，主旨：{subject}")
        return True
    except EmailError as e:
        logger.error(f"發信失敗：{e}")
        return False


def _get_dashboard_url() -> str:
    """從 deploy.py 取得 GitHub Pages URL（未部署則回傳空字串）。"""
    try:
        from deploy import get_dashboard_url
        return get_dashboard_url()
    except Exception:
        return ""


# ── HTML 渲染 ────────────────────────────────────────────────


def _render_html(report: WeeklyReport, date_range: str, dashboard_url: str = "") -> str:
    total = report.total_items
    negative = report.by_sentiment.get("負面", 0)
    negative_pct = f"{negative / total * 100:.1f}%" if total > 0 else "0%"
    gen_str = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")

    stats_block = _stats_block(report)
    keywords_block = _keywords_block(report.top_keywords)
    negative_block = _negative_issues_block(report.negative_concentration)
    anomaly_block = _anomaly_block(report.anomaly_sources)

    # 儀表板按鈕（有 URL 才顯示）
    dashboard_btn = ""
    if dashboard_url:
        dashboard_btn = f"""
    <div style="text-align:center;margin:24px 0 8px;">
      <a href="{dashboard_url}"
         style="display:inline-block;background:linear-gradient(135deg,#3b82f6,#6366f1);
                color:#fff;font-size:14px;font-weight:700;padding:12px 32px;
                border-radius:8px;text-decoration:none;letter-spacing:0.5px;">
        📊 查看完整互動儀表板
      </a>
      <div style="margin-top:8px;font-size:11px;color:#a0aec0;">{dashboard_url}</div>
    </div>"""

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f4f6f9;font-family:'Helvetica Neue',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:24px 0;">
<tr><td align="center">
<table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">

  <!-- Header -->
  <tr><td style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:32px 40px;text-align:center;">
    <div style="font-size:10px;letter-spacing:3px;color:#a0c4ff;text-transform:uppercase;margin-bottom:6px;">Garena 台灣玩家聲音儀表板</div>
    <h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">週報 {date_range}</h1>
    <div style="margin-top:10px;color:#a0c4ff;font-size:13px;">
      本週共收錄 <strong style="color:#fff;">{total}</strong> 則回饋，
      負面比例 <strong style="color:#fc8181;">{negative_pct}</strong>
    </div>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:32px 40px;">

    {dashboard_btn}
    {stats_block}
    {keywords_block}
    {negative_block}
    {anomaly_block}

  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#f7fafc;padding:16px 40px;text-align:center;border-top:1px solid #e2e8f0;">
    <p style="margin:0;color:#a0aec0;font-size:11px;">Garena 台灣玩家聲音儀表板｜{gen_str}｜Powered by Claude AI</p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""


def _stats_block(report: WeeklyReport) -> str:
    """統計摘要：各類別 + 各情緒的橫條視覺化。"""
    total = report.total_items
    if total == 0:
        return '<p style="color:#718096;">本週無回饋資料。</p>'

    # 類別橫條
    cat_rows = ""
    for cat, count in sorted(report.by_category.items(), key=lambda x: -x[1]):
        if count == 0:
            continue
        pct = count / total * 100
        bar_w = max(int(pct * 2), 2)  # 最大 200px
        color, bg = CATEGORY_COLORS.get(cat, ("#2D3748", "#E2E8F0"))
        cat_rows += f"""
        <tr>
          <td style="width:90px;font-size:12px;color:#4a5568;padding:4px 8px 4px 0;">{cat}</td>
          <td style="padding:4px 0;">
            <div style="background:#edf2f7;border-radius:3px;overflow:hidden;height:16px;width:200px;">
              <div style="background:{color};width:{bar_w}px;height:16px;border-radius:3px;"></div>
            </div>
          </td>
          <td style="font-size:12px;color:#718096;padding:4px 0 4px 8px;">{count} 則（{pct:.0f}%）</td>
        </tr>"""

    # 情緒統計
    sent_badges = ""
    for sent in ["正面", "負面", "中性"]:
        count = report.by_sentiment.get(sent, 0)
        color, bg = SENTIMENT_COLORS.get(sent, ("#2D3748", "#E2E8F0"))
        sent_badges += f'<span style="background:{bg};color:{color};font-size:12px;font-weight:700;padding:4px 12px;border-radius:20px;margin-right:8px;">{sent} {count}</span>'

    return f"""
    <div style="margin-bottom:28px;">
      <div style="font-size:11px;font-weight:700;color:#718096;letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;">回饋分類統計</div>
      <table cellpadding="0" cellspacing="0" style="margin-bottom:16px;">{cat_rows}</table>
      <div style="margin-top:8px;">{sent_badges}</div>
    </div>"""


def _keywords_block(top_keywords: list[tuple[str, int]]) -> str:
    """Top 10 關鍵詞視覺化標籤雲。"""
    if not top_keywords:
        return ""

    max_count = top_keywords[0][1] if top_keywords else 1
    badges = ""
    for kw, count in top_keywords:
        size = 11 + int((count / max_count) * 6)  # 11px ~ 17px
        opacity = 0.6 + (count / max_count) * 0.4
        badges += (
            f'<span style="display:inline-block;background:#ebf8ff;color:#2b6cb0;'
            f'font-size:{size}px;font-weight:700;padding:4px 10px;border-radius:20px;'
            f'margin:4px;opacity:{opacity:.2f};">{kw} <span style="font-weight:400;font-size:10px;">{count}</span></span>'
        )

    return f"""
    <div style="margin-bottom:28px;">
      <div style="font-size:11px;font-weight:700;color:#718096;letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;">本週熱門關鍵詞 Top {len(top_keywords)}</div>
      <div style="line-height:1.8;">{badges}</div>
    </div>"""


def _negative_issues_block(clusters: list[dict]) -> str:
    """負評最集中的議題。"""
    if not clusters:
        return ""

    cards = ""
    for c in clusters:
        color, bg = CATEGORY_COLORS.get(c["category"], ("#2D3748", "#E2E8F0"))
        cards += f"""
        <div style="border-left:3px solid {color};background:{bg};border-radius:0 4px 4px 0;padding:10px 14px;margin-bottom:8px;">
          <div style="font-size:12px;font-weight:700;color:{color};margin-bottom:4px;">{c['category']} — {c['count']} 則負評</div>
          <div style="font-size:13px;color:#4a5568;line-height:1.5;">{c['sample']}</div>
        </div>"""

    return f"""
    <div style="margin-bottom:28px;">
      <div style="font-size:11px;font-weight:700;color:#718096;letter-spacing:1px;text-transform:uppercase;margin-bottom:12px;">負評最集中議題</div>
      {cards}
    </div>"""


def _anomaly_block(anomaly_sources: list[str]) -> str:
    """量體異常警示。"""
    if not anomaly_sources:
        return ""

    source_names = {
        "appstore_freefire": "App Store（Free Fire）",
        "appstore_aov": "App Store（傳說對決）",
        "googleplay_freefire": "Google Play（Free Fire）",
        "googleplay_aov": "Google Play（傳說對決）",
        "ptt": "PTT",
        "dcard": "Dcard",
    }
    names = "、".join(source_names.get(s, s) for s in anomaly_sources)

    return f"""
    <div style="background:#fffbeb;border:1px solid #f6e05e;border-radius:6px;padding:14px 16px;margin-bottom:28px;">
      <div style="font-size:12px;font-weight:700;color:#744210;margin-bottom:6px;">⚠ 量體異常警示</div>
      <div style="font-size:13px;color:#744210;line-height:1.6;">
        以下來源本週回饋量超出平均值 2 倍以上，可能與近期活動或重大更新有關：<br>
        <strong>{names}</strong>
      </div>
    </div>"""


# ── 純文字版本 ───────────────────────────────────────────────


def _render_plain(report: WeeklyReport, date_range: str, dashboard_url: str = "") -> str:
    total = report.total_items
    lines = [
        f"Garena 台灣玩家聲音週報 {date_range}",
        "=" * 50,
        f"本週共 {total} 則回饋",
        "",
    ]
    if dashboard_url:
        lines += [f"📊 互動儀表板：{dashboard_url}", ""]

    lines.append("【回饋分類統計】")
    for cat, count in sorted(report.by_category.items(), key=lambda x: -x[1]):
        if count > 0:
            lines.append(f"  {cat}：{count} 則")
    lines.append("")

    lines.append("【情緒分布】")
    for sent in ["正面", "負面", "中性"]:
        lines.append(f"  {sent}：{report.by_sentiment.get(sent, 0)} 則")
    lines.append("")

    if report.top_keywords:
        lines.append("【本週熱門關鍵詞 Top 10】")
        for kw, count in report.top_keywords:
            lines.append(f"  {kw}（{count}次）")
        lines.append("")

    if report.negative_concentration:
        lines.append("【負評最集中議題】")
        for c in report.negative_concentration:
            lines.append(f"  • {c['category']}（{c['count']}則）：{c['sample']}")
        lines.append("")

    if report.anomaly_sources:
        lines.append("【量體異常警示】")
        lines.append(f"  以下來源本週量體異常：{', '.join(report.anomaly_sources)}")
        lines.append("")

    lines.append("─" * 50)
    lines.append(f"由 Garena 台灣玩家聲音儀表板自動生成｜{report.generated_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


# ── SMTP 發送 ────────────────────────────────────────────────


def _send_smtp(subject: str, html_body: str, plain_body: str, config: Config) -> None:
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = config.gmail_sender
    msg["To"] = config.gmail_recipient
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(config.gmail_sender, config.gmail_app_password)
            server.sendmail(config.gmail_sender, config.gmail_recipient, msg.as_string())
    except smtplib.SMTPAuthenticationError as e:
        raise EmailError(
            "Gmail 認證失敗。請確認：\n"
            "1. GMAIL_APP_PASSWORD 是否正確（16 字元，不含空格）\n"
            "2. Gmail 帳號是否已開啟「兩步驟驗證」\n"
            f"原始錯誤：{e}"
        ) from e
    except smtplib.SMTPException as e:
        raise EmailError(f"Gmail SMTP 錯誤：{e}") from e
    except OSError as e:
        raise EmailError(f"網路連線失敗：{e}") from e
