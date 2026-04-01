"""
email_sender.py — 將週報彙整渲染成 HTML 電子報並透過 Gmail SMTP 發送
結構：統計摘要框 → Top 10 關鍵詞 → 負評集中議題 → 量體異常警示
"""
import logging
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

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


def send_weekly_digest(report: WeeklyReport, config: Config,
                       game_summaries: Optional[dict] = None,
                       total_display: Optional[int] = None) -> bool:
    """發送 HTML 週報，成功回傳 True，失敗記錄錯誤回傳 False。
    game_summaries: {"aov": "...", "ff": "..."} 各遊戲 AI 洞察
    total_display:  顯示在標題的則數（優先使用，以與儀表板一致）
    """
    try:
        dashboard_url = _get_dashboard_url()
        date_range = f"{report.week_start.strftime('%Y/%m/%d')} - {report.week_end.strftime('%Y/%m/%d')}"
        subject = f"【Garena 玩家聲音】週報 {date_range}"
        display_total = total_display if total_display is not None else report.total_items
        html_body = _render_html(report, date_range, dashboard_url, game_summaries or {},
                                 display_total=display_total)
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


def _render_html(report: WeeklyReport, date_range: str,
                 dashboard_url: str = "", game_summaries: Optional[dict] = None,
                 display_total: Optional[int] = None) -> str:
    total   = display_total if display_total is not None else report.total_items
    gen_str = report.generated_at.strftime("%Y-%m-%d %H:%M UTC")

    insights_block  = _game_insights_block(game_summaries or {}, date_range)
    cat_block       = _category_stats_block(report)

    dashboard_btn = ""
    if dashboard_url:
        dashboard_btn = (
            '<div style="text-align:center;margin:0 0 28px;">'
            '<a href="{url}" style="display:inline-block;background:linear-gradient(135deg,#3b82f6,#6366f1);'
            'color:#fff;font-size:14px;font-weight:700;padding:12px 32px;'
            'border-radius:8px;text-decoration:none;letter-spacing:0.5px;">'
            '📊 查看即時儀表板</a></div>'
        ).format(url=dashboard_url)

    return (
        '<!DOCTYPE html>\n<html lang="zh-TW">\n'
        '<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>\n'
        '<body style="margin:0;padding:0;background:#f4f6f9;font-family:\'Helvetica Neue\',Arial,sans-serif;">\n'
        '<table width="100%" cellpadding="0" cellspacing="0" style="background:#f4f6f9;padding:24px 0;">\n'
        '<tr><td align="center">\n'
        '<table width="640" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;'
        'overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.08);">\n'

        # Header
        '<tr><td style="background:linear-gradient(135deg,#1a1a2e,#16213e);padding:28px 40px;text-align:center;">'
        '<div style="font-size:10px;letter-spacing:3px;color:#a0c4ff;text-transform:uppercase;margin-bottom:6px;">'
        'Garena 台灣玩家聲音儀表板</div>'
        '<h1 style="margin:0;color:#fff;font-size:22px;font-weight:700;">週報 ' + date_range + '</h1>'
        '<div style="margin-top:8px;color:#a0c4ff;font-size:13px;">'
        '本週共收錄 <strong style="color:#fff;">' + str(total) + '</strong> 則回饋'
        '</div></td></tr>\n'

        # Body
        '<tr><td style="padding:32px 40px;">'
        + dashboard_btn
        + insights_block
        + cat_block
        + '</td></tr>\n'

        # Footer
        '<tr><td style="background:#f7fafc;padding:16px 40px;text-align:center;border-top:1px solid #e2e8f0;">'
        '<p style="margin:0;color:#a0aec0;font-size:11px;">'
        'Garena 台灣玩家聲音儀表板｜' + gen_str + '｜Powered by Claude AI</p>'
        '</td></tr>\n'

        '</table>\n</td></tr>\n</table>\n</body></html>'
    )


def _keywords_wordcloud_block(top_keywords: list[tuple[str, int]]) -> str:
    """熱門關鍵詞：詞雲樣式，高頻詞大字彩色，低頻詞淡灰。"""
    if not top_keywords:
        return ""

    wc_colors = ['#1d4ed8', '#7c3aed', '#0891b2', '#059669', '#d97706', '#dc2626', '#9333ea']
    max_c = top_keywords[0][1] if top_keywords else 1

    # 高低頻交錯排列
    high = [w for w in top_keywords if w[1] >= max_c * 0.2]
    low  = [w for w in top_keywords if w[1] <  max_c * 0.2]
    shuffled = []
    for i in range(max(len(high), len(low))):
        if i < len(high): shuffled.append(high[i])
        if i < len(low):  shuffled.append(low[i])

    badges = ""
    valigns = ['middle', 'bottom', 'top', 'middle', 'text-bottom', 'text-top']
    for idx, (kw, count) in enumerate(shuffled):
        ci = sum(ord(ch) for ch in kw)
        va = valigns[(ci + idx * 2) % len(valigns)]
        mt = ci % 6
        if count < max_c * 0.2:
            badges += (
                '<span style="display:inline-block;font-size:11px;color:#cbd5e1;'
                'padding:0 4px;margin:{}px 2px 0;vertical-align:{};">{}</span>'
            ).format(mt, va, kw)
        else:
            size   = 12 + int((count / max_c) * 10)
            weight = '700' if count >= max_c * 0.5 else '500'
            color  = wc_colors[ci % len(wc_colors)]
            badges += (
                '<span style="display:inline-block;font-size:{}px;font-weight:{};color:{};'
                'padding:0 5px;margin:{}px 1px 0;vertical-align:{};">{}</span>'
            ).format(size, weight, color, mt, va, kw)

    return (
        '<div style="margin-bottom:28px;">'
        '<div style="font-size:11px;font-weight:700;color:#718096;letter-spacing:1px;'
        'text-transform:uppercase;margin-bottom:12px;">本週熱門關鍵詞</div>'
        '<div style="text-align:center;line-height:2.2;padding:8px 0;">'
        + badges +
        '</div></div>'
    )


def _game_insights_block(game_summaries: dict, date_range: str = "") -> str:
    """按遊戲分區塊顯示 AI 洞察，格式同儀表板的類別標籤。"""
    import re
    if not game_summaries:
        return ""

    _CAT_COLORS = {
        "客服問題": "#ea580c", "活動反應": "#2563eb",
        "Bug回報":  "#dc2626", "付費體驗": "#d97706",
        "遊戲體驗": "#16a34a", "其他":     "#7c3aed",
    }
    GAME_LABELS = {
        "aov": ("⚔️ 傳說對決", "#1d4ed8", "#eff6ff"),
        "ff":  ("🔥 Free Fire", "#c2410c", "#fff7ed"),
    }

    period_note = "（{}）".format(date_range) if date_range else ""
    html = (
        '<div style="margin-bottom:28px;">'
        '<div style="font-size:11px;font-weight:700;color:#718096;letter-spacing:1px;'
        'text-transform:uppercase;margin-bottom:16px;">'
        '社群觀察重點 ' + period_note + '</div>'
    )

    for game_key in ["aov", "ff"]:
        summary = game_summaries.get(game_key, "")
        if not summary:
            continue
        label, hdr_color, hdr_bg = GAME_LABELS[game_key]

        html += (
            '<div style="border:1px solid {border};border-radius:8px;'
            'margin-bottom:20px;overflow:hidden;">'
            # 置中標題列
            '<div style="background:{bg};padding:12px 20px;'
            'border-bottom:1px solid {border};text-align:center;">'
            '<span style="font-size:15px;font-weight:700;color:{c};">{label}</span>'
            '</div>'
            # 內容區塊，左右留白更寬
            '<div style="padding:16px 24px;">'
        ).format(bg=hdr_bg, c=hdr_color, label=label,
                 border=hdr_color + "33")

        for line in summary.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r'[【\[](.{2,6})[】\]]\s*[：:]?\s*(.*)', line)
            if m:
                cat     = m.group(1).strip()
                content = (m.group(2) or "").strip()
                color   = _CAT_COLORS.get(cat, "#475569")
                html += (
                    '<table cellpadding="0" cellspacing="0" style="margin-bottom:12px;width:100%;">'
                    '<tr>'
                    '<td style="width:76px;padding-right:16px;vertical-align:middle;">'
                    '<span style="display:block;background:{c}15;color:{c};font-size:11px;'
                    'font-weight:700;padding:4px 8px;border-radius:4px;border:1px solid {c}40;'
                    'white-space:nowrap;text-align:center;">{cat}</span>'
                    '</td>'
                    '<td style="vertical-align:top;">'
                    '<span style="font-size:13px;color:#334155;line-height:1.7;">{content}</span>'
                    '</td>'
                    '</tr></table>'
                ).format(c=color, cat=cat,
                         content=content.replace("<", "&lt;").replace(">", "&gt;"))
            else:
                clean = line.lstrip("•·-–—1234567890. ")
                if clean:
                    html += (
                        '<div style="display:flex;gap:8px;margin-bottom:10px;">'
                        '<span style="color:{c};font-size:16px;line-height:1.4;flex-shrink:0;">•</span>'
                        '<span style="font-size:13px;color:#334155;line-height:1.7;">{txt}</span>'
                        '</div>'
                    ).format(c=hdr_color,
                             txt=clean.replace("<", "&lt;").replace(">", "&gt;"))

        html += '</div></div>'

    html += '</div>'
    return html


def _category_stats_block(report: WeeklyReport) -> str:
    """各類別回饋數橫條（不含情緒）。"""
    total = report.total_items
    if total == 0:
        return '<p style="color:#718096;">本週無回饋資料。</p>'

    cat_rows = ""
    for cat, count in sorted(report.by_category.items(), key=lambda x: -x[1]):
        if count == 0:
            continue
        pct = count / total * 100
        bar_w = max(int(pct * 2), 2)
        color, _ = CATEGORY_COLORS.get(cat, ("#2D3748", "#E2E8F0"))
        cat_rows += (
            '<tr>'
            '<td style="width:80px;font-size:12px;color:#4a5568;padding:4px 8px 4px 0;">{cat}</td>'
            '<td style="padding:4px 0;">'
            '<div style="background:#edf2f7;border-radius:3px;overflow:hidden;height:14px;width:180px;">'
            '<div style="background:{color};width:{bar_w}px;height:14px;border-radius:3px;"></div>'
            '</div></td>'
            '<td style="font-size:11px;color:#718096;padding:4px 0 4px 8px;">{count} 則</td>'
            '</tr>'
        ).format(cat=cat, color=color, bar_w=bar_w, count=count)

    return (
        '<div style="margin-bottom:28px;">'
        '<div style="font-size:11px;font-weight:700;color:#718096;letter-spacing:1px;'
        'text-transform:uppercase;margin-bottom:12px;">回饋分類統計</div>'
        '<table cellpadding="0" cellspacing="0">' + cat_rows + '</table>'
        '</div>'
    )


def _negative_issues_block(clusters: list[dict]) -> str:
    """負評最集中的議題（每類顯示多則引言）。"""
    if not clusters:
        return ""

    cards = ""
    for c in clusters:
        color, bg = CATEGORY_COLORS.get(c["category"], ("#2D3748", "#E2E8F0"))
        samples = c.get("samples") or ([c["sample"]] if c.get("sample") else [])
        quotes = ""
        for i, s in enumerate(samples):
            prefix = "→" if i == 0 else "·"
            style = "font-size:12px;color:#4a5568;line-height:1.6;"
            if i > 0:
                style += "margin-top:3px;padding-left:10px;"
            quotes += f'<div style="{style}">{prefix} {s}</div>'
        cards += (
            f'<div style="border-left:3px solid {color};background:{bg};'
            f'border-radius:0 4px 4px 0;padding:10px 14px;margin-bottom:10px;">'
            f'<div style="font-size:12px;font-weight:700;color:{color};margin-bottom:6px;">'
            f'{c["category"]} — {c["count"]} 則負評</div>'
            f'{quotes}</div>'
        )

    return (
        '<div style="margin-bottom:28px;">'
        '<div style="font-size:11px;font-weight:700;color:#718096;letter-spacing:1px;'
        'text-transform:uppercase;margin-bottom:12px;">負評最集中議題</div>'
        + cards + '</div>'
    )


def _activity_voices_block(activity_voices: list[dict]) -> str:
    """活動反應聲量區塊：反映玩家對近期活動的正負評。"""
    if not activity_voices:
        return ""

    v = activity_voices[0]
    total = v.get("total", 0)
    if total == 0:
        return ""

    pos   = v.get("pos", 0)
    neg   = v.get("neg", 0)
    pos_samples = v.get("pos_samples", [])
    neg_samples = v.get("neg_samples", [])

    pos_html = ""
    for s in pos_samples:
        pos_html += f'<div style="font-size:12px;color:#276749;line-height:1.6;margin-top:3px;">👍 {s}</div>'

    neg_html = ""
    for s in neg_samples:
        neg_html += f'<div style="font-size:12px;color:#742A2A;line-height:1.6;margin-top:3px;">👎 {s}</div>'

    if not pos_html and not neg_html:
        return ""

    return f"""
    <div style="background:#f0fff4;border:1px solid #9ae6b4;border-radius:6px;padding:14px 16px;margin-bottom:28px;">
      <div style="font-size:11px;font-weight:700;color:#276749;letter-spacing:1px;text-transform:uppercase;margin-bottom:8px;">
        📣 本週活動聲量（{total} 則）
      </div>
      <div style="font-size:12px;color:#276749;margin-bottom:6px;">
        正面 {pos} 則 ／ 負面 {neg} 則
      </div>
      {pos_html}{neg_html}
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
