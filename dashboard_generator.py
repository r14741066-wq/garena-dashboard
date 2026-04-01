"""
dashboard_generator.py — 從 state.db 讀取過去 7 天資料，生成靜態 HTML 儀表板
主題：白底簡約風
- 趨勢圖使用 published_at（評論原始發布日期）
- 詞雲（wordcloud2.js）
- 渠道分析卡片
- 遊戲分頁篩選
"""
import argparse
import json
import sqlite3
import subprocess
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH       = Path(__file__).parent / "state.db"
OUTPUT_PATH   = Path(__file__).parent / "dashboard.html"
DAYS_LOOKBACK = 7

SOURCE_LABELS = {
    "appstore_freefire":   "App Store · Free Fire",
    "appstore_aov":        "App Store · 傳說對決",
    "googleplay_freefire": "Google Play · Free Fire",
    "googleplay_aov":      "Google Play · 傳說對決",
    "ptt":                 "PTT",
    "dcard":               "Dcard",
}
SOURCE_GAME = {
    "appstore_freefire":   "freefire",
    "appstore_aov":        "aov",
    "googleplay_freefire": "freefire",
    "googleplay_aov":      "aov",
    "ptt":                 "mixed",
    "dcard":               "mixed",
}
CATEGORY_COLORS = {
    "客服問題": "#ea580c",
    "活動反應": "#2563eb",
    "Bug回報":  "#dc2626",
    "付費體驗": "#d97706",
    "遊戲體驗": "#16a34a",
    "其他":     "#7c3aed",
}
SENTIMENT_COLORS = {
    "正面": "#16a34a",
    "負面": "#dc2626",
    "中性": "#94a3b8",
}
FREEFIRE_KEYWORDS = ["free fire", "freefire", "荒野行動"]
AOV_KEYWORDS      = ["傳說對決", "aov", "arena of valor"]


def generate_ai_summary(data, game_label="傳說對決"):
    """
    呼叫 Claude 產生本週社群洞察重點，以類別標籤分段。
    輸出格式：【類別】說明文字（每點一行）
    """
    if not data:
        return "本週尚無足夠資料進行分析。"

    cat_counter = Counter(d.get("category", "其他") for d in data)
    active_cats = [c for c, n in cat_counter.most_common() if n > 0]

    kw_counter = Counter()
    for d in data:
        for kw in d.get("keywords", []):
            if kw:
                kw_counter[kw] += 1
    top_kw = "、".join(kw for kw, _ in kw_counter.most_common(15))

    cat_summary = "　".join(
        "{}{}則".format(k, v)
        for k, v in sorted(cat_counter.items(), key=lambda x: -x[1])
        if v > 0
    )

    # 每個類別抓代表性樣本
    by_cat = {}
    for d in data:
        c = d.get("category", "其他")
        by_cat.setdefault(c, [])
        s = (d.get("summary") or (d.get("body") or "")[:120]).strip()
        if s and len(by_cat[c]) < 5:
            by_cat[c].append(s)

    samples_text = ""
    for cat in active_cats[:6]:
        items = by_cat.get(cat, [])
        if items:
            samples_text += "\n【{}】\n".format(cat)
            for s in items[:4]:
                samples_text += "  - {}\n".format(s)

    prompt = (
        "以下是 Garena 台灣「{}」玩家本週共 {} 則社群回饋（App Store、Google Play、PTT、Dcard）。\n\n"
        "熱門關鍵詞：{}\n各類別數量：{}\n\n"
        "各類別代表性留言：\n{}\n\n"
        "請用繁體中文，針對有資料的類別，每類寫出 1～2 句重點洞察。\n"
        "格式務必嚴格遵守：每行以【類別名稱】開頭（如【遊戲體驗】【Bug回報】【活動反應】【付費體驗】【客服問題】），"
        "接著直接寫說明，不要有其他格式。\n"
        "重點包含：① 玩家在討論什麼、有什麼爭議；② 行銷活動的熱度是否被點燃；③ 需要團隊及早處理的問題。\n"
        "直接切重點，不要廢話，沒資料的類別直接跳過。"
    ).format(
        game_label, len(data), top_kw, cat_summary, samples_text
    )

    try:
        import anthropic
        from config import load_config
        config = load_config()
        client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
        return msg.content[0].text.strip()
    except Exception as e:
        lines = []
        for cat, n in cat_counter.most_common(4):
            if n > 0:
                samples = by_cat.get(cat, [])
                sample_text = "　例：「{}」".format(samples[0][:40]) if samples else ""
                lines.append("【{}】本週共 {} 則，{}{}".format(cat, n, "回饋量較高，建議關注。", sample_text))
        return "\n".join(lines) if lines else "本週資料不足，無法產生摘要。"


def get_game(item):
    source = item.get("source", "")
    if "freefire" in source:
        return "freefire"
    if "aov" in source:
        return "aov"
    text = " ".join([
        (item.get("title") or ""),
        (item.get("body") or "")[:300],
        " ".join(item.get("keywords", [])),
    ]).lower()
    for kw in FREEFIRE_KEYWORDS:
        if kw in text:
            return "freefire"
    for kw in AOV_KEYWORDS:
        if kw in text:
            return "aov"
    return "unknown"


def load_data(days=DAYS_LOOKBACK):
    if not DB_PATH.exists():
        return []
    today = datetime.utcnow().date()
    since = (today - timedelta(days=days - 1)).isoformat()  # 用發布日起始日（含今天共 days 天）
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM analyses WHERE published_at >= ? ORDER BY published_at DESC",
            (since,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["keywords"] = json.loads(d["keywords"]) if d["keywords"] else []
        except Exception:
            d["keywords"] = []
        d["game"] = get_game(d)
        result.append(d)
    return result


def aggregate(data, days=DAYS_LOOKBACK):
    total        = len(data)
    by_category  = Counter(d.get("category", "其他") for d in data)
    by_sentiment = Counter(d.get("sentiment", "中性") for d in data)
    by_source    = Counter(d.get("source", "") for d in data)

    today = datetime.utcnow().date()
    daily = {}
    for i in range(days):
        day = today - timedelta(days=days - 1 - i)
        daily[day.strftime("%m/%d")] = 0
    for d in data:
        try:
            day     = datetime.fromisoformat(d["published_at"]).date()
            day_str = day.strftime("%m/%d")
            if day_str in daily:
                daily[day_str] += 1
        except Exception:
            pass

    kw_counter = Counter()
    for d in data:
        for kw in d.get("keywords", []):
            if kw:
                kw_counter[kw] += 1
    top_kw = kw_counter.most_common(40)

    cat_labels = list(CATEGORY_COLORS.keys())
    cat_vals   = [by_category.get(c, 0) for c in cat_labels]
    cat_colors = [CATEGORY_COLORS[c] for c in cat_labels]

    sent_labels = ["正面", "負面", "中性"]
    sent_vals   = [by_sentiment.get(s, 0) for s in sent_labels]

    src_entries = sorted(
        [(SOURCE_LABELS.get(k, k), v) for k, v in by_source.items() if v > 0],
        key=lambda x: -x[1]
    )

    neg = by_sentiment.get("負面", 0)
    pos = by_sentiment.get("正面", 0)

    recent = []
    for d in data[:30]:
        cat  = d.get("category", "—")
        sent = d.get("sentiment", "—")
        src  = SOURCE_LABELS.get(d.get("source", ""), d.get("source", ""))
        summ = (d.get("summary") or d.get("body", "")[:80]).replace("<", "&lt;").replace(">", "&gt;")
        kws  = "、".join(d.get("keywords", []))
        date = (d.get("published_at") or "")[:10]
        url  = d.get("url", "#")
        recent.append({
            "cat": cat, "sent": sent, "src": src,
            "summ": summ, "kws": kws, "date": date, "url": url,
            "game": d.get("game", "unknown"),
            "cat_color":  CATEGORY_COLORS.get(cat, "#7c3aed"),
            "sent_color": SENTIMENT_COLORS.get(sent, "#94a3b8"),
        })

    return {
        "total":          total,
        "neg":            neg,
        "pos":            pos,
        "neg_pct":        round(neg / total * 100) if total else 0,
        "pos_pct":        round(pos / total * 100) if total else 0,
        "active_sources": len([v for v in by_source.values() if v > 0]),
        "cat_labels":     cat_labels,
        "cat_vals":       cat_vals,
        "cat_colors":     cat_colors,
        "sent_labels":    sent_labels,
        "sent_vals":      sent_vals,
        "daily_labels":   list(daily.keys()),
        "daily_vals":     list(daily.values()),
        "src_labels":     [e[0] for e in src_entries],
        "src_vals":       [e[1] for e in src_entries],
        "wordcloud":      [[kw, cnt] for kw, cnt in top_kw],
        "recent":         recent,
    }


def channel_breakdown(data):
    sources = {}
    for d in data:
        src = d.get("source", "unknown")
        if src not in sources:
            sources[src] = []
        sources[src].append(d)

    result = []
    for src, items in sorted(sources.items(), key=lambda x: -len(x[1])):
        total = len(items)
        sent  = Counter(d.get("sentiment", "中性") for d in items)
        cats  = Counter(d.get("category", "其他") for d in items)
        kw    = Counter()
        for d in items:
            for k in d.get("keywords", []):
                if k:
                    kw[k] += 1

        neg = sent.get("負面", 0)
        pos = sent.get("正面", 0)
        neg_pct = round(neg / total * 100) if total else 0
        pos_pct = round(pos / total * 100) if total else 0
        top_keywords = [k for k, _ in kw.most_common(5)]

        # 負評類別分布
        neg_cats = Counter(
            d.get("category", "其他") for d in items if d.get("sentiment") == "負面"
        ).most_common(3)
        pos_cats = Counter(
            d.get("category", "其他") for d in items if d.get("sentiment") == "正面"
        ).most_common(1)

        # 取1-2則負評摘要作引證
        neg_samples = []
        for d in items:
            if d.get("sentiment") == "負面":
                s = (d.get("summary") or d.get("body", "")[:40]).strip()
                if s and s not in neg_samples:
                    neg_samples.append(s)
            if len(neg_samples) >= 2:
                break

        if neg_pct >= 70:
            cats_str = "、".join(
                "{}（{} 則）".format(c, n) for c, n in neg_cats[:2]
            )
            summary = "本週 {} 則回饋中 {}% 為負評。集中議題：{}。".format(
                total, neg_pct, cats_str
            )
            if neg_samples:
                summary += "　例：「{}」".format(neg_samples[0][:40])
        elif pos_pct >= 50:
            pos_cat = pos_cats[0][0] if pos_cats else "遊戲體驗"
            summary = "整體評價偏正面（正面 {}%）。玩家對{}給予好評。".format(pos_pct, pos_cat)
            if neg_cats:
                summary += "　負評關注：{}。".format(neg_cats[0][0])
        else:
            cats_str = "、".join("{}（{} 則）".format(c, n) for c, n in neg_cats[:2]) if neg_cats else "—"
            summary = "回饋分散（負 {}%、正 {}%）。負評主要集中於{}。".format(
                neg_pct, pos_pct, cats_str
            )

        result.append({
            "source":       src,
            "name":         SOURCE_LABELS.get(src, src),
            "game":         SOURCE_GAME.get(src, "mixed"),
            "total":        total,
            "neg_pct":      neg_pct,
            "pos_pct":      pos_pct,
            "neu_pct":      100 - neg_pct - pos_pct,
            "top_keywords": top_keywords,
            "summary":      summary,
        })
    return result


def _render_trend_svg(daily_labels, daily_vals):
    """Python 端生成 SVG 折線圖"""
    W, H, PL, PR, PT, PB = 500, 150, 32, 10, 10, 22
    plotW, plotH = W - PL - PR, H - PT - PB
    n = len(daily_labels)
    max_v = max(daily_vals) if daily_vals else 1
    max_v = max(int(max_v * 1.1), 1)
    step_x = plotW / (n - 1) if n > 1 else 0

    svg = '<svg viewBox="0 0 {W} {H}" xmlns="http://www.w3.org/2000/svg" style="width:100%;height:100%;">'.format(W=W, H=H)

    # Grid + Y labels
    for g in range(5):
        gy = H - PB - (g / 4) * plotH
        gv = round(max_v * g / 4)
        svg += '<line x1="{PL}" y1="{gy}" x2="{x2}" y2="{gy}" stroke="#f1f5f9" stroke-width="1"/>'.format(PL=PL, gy=round(gy, 1), x2=W - PR)
        svg += '<text x="{x}" y="{y}" text-anchor="end" fill="#94a3b8" font-size="9">{v}</text>'.format(x=PL - 4, y=round(gy + 3, 1), v=gv)

    pts = []
    for i in range(n):
        x = PL + i * step_x
        y = H - PB - (daily_vals[i] / max_v) * plotH
        pts.append((round(x, 1), round(y, 1)))

    # Area fill
    area = "M {PL} {base}".format(PL=PL, base=H - PB)
    for x, y in pts:
        area += " L {} {}".format(x, y)
    area += " L {} {} Z".format(pts[-1][0] if pts else PL, H - PB)
    svg += '<path d="{d}" fill="rgba(37,99,235,0.06)"/>'.format(d=area)

    # Polyline
    poly_pts = " ".join("{},{}".format(x, y) for x, y in pts)
    svg += '<polyline points="{pts}" fill="none" stroke="#2563eb" stroke-width="2" stroke-linejoin="round"/>'.format(pts=poly_pts)

    # Dots + values + X labels
    for i, (x, y) in enumerate(pts):
        svg += '<circle cx="{x}" cy="{y}" r="3" fill="#2563eb"/>'.format(x=x, y=y)
        if daily_vals[i] > 0:
            svg += '<text x="{x}" y="{y}" text-anchor="middle" fill="#2563eb" font-size="9" font-weight="600">{v}</text>'.format(x=x, y=round(y - 7, 1), v=daily_vals[i])
        svg += '<text x="{x}" y="{y}" text-anchor="middle" fill="#94a3b8" font-size="9">{lb}</text>'.format(x=x, y=H - 4, lb=daily_labels[i])

    svg += '</svg>'
    return svg


def _render_cat_bars(cat_labels, cat_vals, cat_colors):
    """Python 端生成分類橫條圖"""
    max_v = max(cat_vals) if cat_vals else 1
    html = ""
    for label, val, color in zip(cat_labels, cat_vals, cat_colors):
        pct = round(val / max_v * 100) if max_v else 0
        html += (
            '<div style="display:flex;align-items:center;margin-bottom:5px;">'
            '<span style="width:48px;font-size:11px;color:#475569;text-align:right;padding-right:6px;flex-shrink:0;">{lb}</span>'
            '<div style="flex:1;height:12px;background:#f1f5f9;border-radius:3px;overflow:hidden;">'
            '<div style="height:100%;width:{pct}%;background:{c};border-radius:3px;"></div></div>'
            '<span style="width:24px;font-size:10px;color:#94a3b8;text-align:right;padding-left:4px;">{v}</span></div>'
        ).format(lb=label, pct=pct, c=color, v=val)
    return html


def _render_sent_donut(sent_labels, sent_vals):
    """Python 端生成情緒甜甜圈"""
    colors = ["#16a34a", "#dc2626", "#94a3b8"]
    total = sum(sent_vals)
    if total == 0:
        return '<span style="color:#94a3b8;font-size:12px;">無資料</span>'

    parts, cum = [], 0.0
    for i, val in enumerate(sent_vals):
        pct = val / total * 100
        parts.append("{c} {s}% {e}%".format(c=colors[i], s=round(cum, 1), e=round(cum + pct, 1)))
        cum += pct

    html = (
        '<div style="width:110px;height:110px;border-radius:50%;margin:0 auto;position:relative;'
        'background:conic-gradient({grad});">'
        '<div style="width:70px;height:70px;border-radius:50%;background:#fff;'
        'position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);'
        'display:flex;align-items:center;justify-content:center;flex-direction:column;">'
        '<div style="font-size:16px;font-weight:700;color:#0f172a;">{total}</div>'
        '<div style="font-size:9px;color:#94a3b8;">則</div></div></div>'
    ).format(grad=",".join(parts), total=total)

    html += '<div style="display:flex;justify-content:center;gap:10px;margin-top:8px;">'
    for i, label in enumerate(sent_labels):
        pct = round(sent_vals[i] / total * 100)
        html += (
            '<span style="font-size:10px;color:#475569;">'
            '<span style="display:inline-block;width:7px;height:7px;border-radius:50%;background:{c};margin-right:2px;vertical-align:middle;"></span>'
            '{lb} {pct}%</span>'
        ).format(c=colors[i], lb=label, pct=pct)
    html += '</div>'
    return html


def _render_src_bars(src_labels, src_vals):
    """Python 端生成來源橫條"""
    if not src_labels:
        return '<span style="color:#94a3b8;font-size:12px;">本週無資料</span>'
    max_v = max(src_vals) if src_vals else 1
    html = ""
    for label, val in zip(src_labels, src_vals):
        pct = round(val / max_v * 100) if max_v else 0
        html += (
            '<div style="margin-bottom:8px;">'
            '<div style="display:flex;justify-content:space-between;font-size:11px;color:#475569;margin-bottom:2px;">'
            '<span>{lb}</span><span style="font-weight:600;color:#0f172a;">{v} 則</span></div>'
            '<div style="height:7px;background:#f1f5f9;border-radius:4px;overflow:hidden;">'
            '<div style="height:100%;width:{pct}%;background:#3b82f6;border-radius:4px;"></div>'
            '</div></div>'
        ).format(lb=label, v=val, pct=pct)
    return html


def _render_wordcloud(wordcloud):
    """Python 端生成詞雲：雲狀排列，低頻詞統一淡灰，高頻詞彩色大字"""
    wc_colors = ['#1d4ed8', '#7c3aed', '#0891b2', '#059669', '#d97706', '#dc2626', '#9333ea', '#0284c7']
    if not wordcloud:
        return '<span style="color:#cbd5e1;font-size:12px;">本週無關鍵詞資料</span>'
    max_c = wordcloud[0][1] if wordcloud else 1

    # 打散排列：將詞分成高/低兩組，交錯排列讓視覺更像雲
    high = [w for w in wordcloud if w[1] >= max_c * 0.15]
    low  = [w for w in wordcloud if w[1] <  max_c * 0.15]
    shuffled = []
    for i in range(max(len(high), len(low))):
        if i < len(high): shuffled.append(high[i])
        if i < len(low):  shuffled.append(low[i])

    valigns = ['baseline', 'text-bottom', 'middle', 'top', 'text-top', 'bottom']
    html = ""
    for idx, (word, count) in enumerate(shuffled):
        ci = sum(ord(ch) for ch in word)
        va  = valigns[(ci + idx * 3) % len(valigns)]
        mt  = (ci % 7)  # 0–6px margin-top 增加高低錯落感

        if count < max_c * 0.15:
            # 低頻詞：統一淡灰
            html += (
                '<span style="font-size:11px;color:#cbd5e1;padding:0 3px;'
                'cursor:default;white-space:nowrap;vertical-align:{va};'
                'display:inline-block;margin-top:{mt}px;" title="{cnt} 次">{word}</span>'
            ).format(va=va, mt=mt, cnt=count, word=word)
        else:
            size   = round(12 + (count / max_c) * 24)
            weight = '700' if count >= max_c * 0.4 else '500'
            color  = wc_colors[ci % len(wc_colors)]
            html += (
                '<span style="font-size:{sz}px;color:{c};font-weight:{w};padding:0 4px;'
                'cursor:default;white-space:nowrap;vertical-align:{va};'
                'display:inline-block;margin-top:{mt}px;" title="{cnt} 次">{word}</span>'
            ).format(sz=size, c=color, w=weight, va=va, mt=mt, cnt=count, word=word)
    return html


def _render_table_rows(recent):
    """Python 端生成明細表格列"""
    html = ""
    for d in recent:
        summ = d["summ"].replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        html += (
            '<tr style="border-bottom:1px solid #f1f5f9;">'
            '<td style="padding:5px 8px;color:#94a3b8;white-space:nowrap;font-size:11px;">{date}</td>'
            '<td style="padding:5px 8px;"><span style="background:{cc}18;color:{cc};font-size:10px;font-weight:600;padding:2px 7px;border-radius:20px;">{cat}</span></td>'
            '<td style="padding:5px 8px;"><span style="background:{sc}18;color:{sc};font-size:10px;font-weight:600;padding:2px 7px;border-radius:20px;">{sent}</span></td>'
            '<td style="padding:5px 8px;color:#334155;max-width:280px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">'
            '<a href="{url}" target="_blank" style="color:inherit;text-decoration:none;">{summ}</a></td>'
            '<td style="padding:5px 8px;color:#94a3b8;font-size:11px;">{kws}</td>'
            '<td style="padding:5px 8px;color:#cbd5e1;font-size:10px;white-space:nowrap;">{src}</td></tr>'
        ).format(
            date=d["date"], cc=d["cat_color"], cat=d["cat"],
            sc=d["sent_color"], sent=d["sent"], url=d["url"],
            summ=summ, kws=d["kws"], src=d["src"],
        )
    return html


def _render_channel_cards(channels):
    """Python 端生成渠道卡片"""
    html = ""
    for ch in channels:
        kw_tags = "".join(
            '<span style="background:#eff6ff;color:#1d4ed8;font-size:10px;padding:2px 8px;'
            'border-radius:20px;margin:2px 2px 0 0;display:inline-block;border:1px solid #bfdbfe;">'
            + kw + '</span>'
            for kw in ch["top_keywords"]
        )
        neg_w, pos_w = ch["neg_pct"], ch["pos_pct"]
        neu_w = max(100 - neg_w - pos_w, 0)
        html += (
            '<div class="ch" data-g="{game}" '
            'style="background:#fff;border:1px solid #e2e8f0;border-radius:8px;padding:14px;box-shadow:0 1px 3px rgba(0,0,0,.06);">'
            '<div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:6px;">'
            '<div style="font-size:12px;font-weight:700;color:#0f172a;">{name}</div>'
            '<div style="font-size:11px;color:#64748b;">{total} 則</div></div>'
            '<div style="display:flex;height:4px;border-radius:4px;overflow:hidden;margin-bottom:8px;">'
            '<div style="width:{neg_w}%;background:#dc2626;"></div>'
            '<div style="width:{neu_w}%;background:#e2e8f0;"></div>'
            '<div style="width:{pos_w}%;background:#16a34a;"></div></div>'
            '<div style="font-size:11px;color:#64748b;margin-bottom:6px;">'
            '<span style="color:#dc2626;font-weight:600;">負 {neg_w}%</span>&nbsp;·&nbsp;'
            '<span style="color:#16a34a;font-weight:600;">正 {pos_w}%</span></div>'
            '<div style="font-size:11px;color:#475569;margin-bottom:8px;line-height:1.4;">{summary}</div>'
            '<div>{kw_tags}</div></div>'
        ).format(
            game=ch["game"], name=ch["name"], total=ch["total"],
            neg_w=neg_w, neu_w=neu_w, pos_w=pos_w,
            summary=ch["summary"], kw_tags=kw_tags,
        )
    return html


def _render_ai_summary_html(text):
    """將 AI 摘要文字轉成有樣式的 HTML，支援【類別】標籤彩色徽章"""
    import re
    _CAT_COLORS = {
        "客服問題": "#ea580c",
        "活動反應": "#2563eb",
        "Bug回報":  "#dc2626",
        "付費體驗": "#d97706",
        "遊戲體驗": "#16a34a",
        "其他":     "#7c3aed",
    }
    if not text:
        return '<span style="color:#94a3b8;">（無摘要）</span>'
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    html = ""
    for line in lines:
        m = re.match(r'[【\[](.{2,6})[】\]]\s*[：:]?\s*(.*)', line)
        if m:
            cat     = m.group(1).strip()
            content = (m.group(2) or "").strip()
            color   = _CAT_COLORS.get(cat, "#475569")
            html += (
                '<div style="display:flex;gap:12px;margin-bottom:9px;align-items:flex-start;">'
                '<span style="display:inline-flex;align-items:center;'
                'background:{c}15;color:{c};font-size:10px;font-weight:700;'
                'padding:3px 9px;border-radius:3px;border:1px solid {c}40;'
                'flex-shrink:0;white-space:nowrap;line-height:1;">{cat}</span>'
                '<span style="font-size:13px;color:#334155;line-height:1.6;">{content}</span>'
                '</div>'
            ).format(c=color, cat=cat,
                     content=content.replace("<", "&lt;").replace(">", "&gt;"))
        else:
            clean = line.lstrip("•·-–—1234567890. ")
            if not clean:
                continue
            html += (
                '<div style="display:flex;gap:8px;margin-bottom:7px;align-items:flex-start;">'
                '<span style="color:#2563eb;font-size:14px;line-height:1.4;flex-shrink:0;">•</span>'
                '<span style="font-size:13px;color:#334155;line-height:1.6;">{}</span>'
                '</div>'
            ).format(clean.replace("<", "&lt;").replace(">", "&gt;"))
    return html


def _render_game_view(agg, channels, game_filter, ai_summary=""):
    """生成一個遊戲分頁的完整內容（純 HTML，無 JS）"""
    trend_svg  = _render_trend_svg(agg["daily_labels"], agg["daily_vals"])
    cat_bars   = _render_cat_bars(agg["cat_labels"], agg["cat_vals"], agg["cat_colors"])
    src_bars   = _render_src_bars(agg["src_labels"], agg["src_vals"])
    wc_html    = _render_wordcloud(agg["wordcloud"])
    summary_html = _render_ai_summary_html(ai_summary)

    if game_filter == "all":
        ch_cards = _render_channel_cards(channels)
    else:
        filtered = [c for c in channels if c["game"] == game_filter or c["game"] == "mixed"]
        ch_cards = _render_channel_cards(filtered)

    return (
        '<!-- 頂部：總數 + AI 洞察 -->'
        '<div class="g-summary">'
        '<div class="card" style="text-align:center;display:flex;flex-direction:column;'
        'justify-content:center;align-items:center;">'
        '<div class="label-sm">本週回饋</div>'
        '<div style="font-size:2.4rem;font-weight:800;color:#0f172a;line-height:1;">{total}</div>'
        '<div style="font-size:11px;color:#94a3b8;margin-top:4px;">則</div>'
        '</div>'
        '<div class="card">'
        '<div class="label-sm" style="margin-bottom:10px;">本週社群重點洞察</div>'
        '{summary}'
        '</div>'
        '</div>'

        '<!-- 渠道分析 -->'
        '<div style="margin-bottom:6px;">'
        '<div class="label-sm" style="margin-bottom:8px;">各渠道分析</div></div>'
        '<div class="g-channels">{ch}</div>'

        '<!-- 圖表：趨勢 + 類別 -->'
        '<div class="g-charts">'
        '<div class="card"><div class="label-sm">每日回饋量趨勢</div>'
        '<div style="height:160px;">{trend}</div></div>'
        '<div class="card"><div class="label-sm">回饋分類</div>{cat}</div>'
        '</div>'

        '<!-- 詞雲 + 來源 -->'
        '<div class="g-wc">'
        '<div class="card"><div class="label-sm">熱門關鍵詞</div>'
        '<div style="text-align:center;line-height:2;padding:4px 2px;">{wc}</div></div>'
        '<div class="card"><div class="label-sm">各平台回饋量</div>'
        '<div style="padding-top:4px;">{src}</div></div>'
        '</div>'
    ).format(
        total=agg["total"],
        summary=summary_html,
        trend=trend_svg, cat=cat_bars,
        wc=wc_html, src=src_bars, ch=ch_cards,
    )


def generate_html(agg_all, agg_ff, agg_aov, channels, date_range,
                  summary_all="", summary_ff="", summary_aov=""):
    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    # 只保留兩個遊戲分頁，不做全部遊戲
    view_ff  = _render_game_view(agg_ff,  channels, "freefire", summary_ff)
    view_aov = _render_game_view(agg_aov, channels, "aov",      summary_aov)

    css = """
  body { background:#f8fafc; color:#0f172a; font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Arial,sans-serif; margin:0; }
  .card { background:#fff; border-radius:8px; padding:14px; border:1px solid #e2e8f0; box-shadow:0 1px 3px rgba(0,0,0,.05); }
  .stat-num { font-size:1.5rem; font-weight:800; line-height:1; }
  .label-sm { font-size:10px; font-weight:600; color:#94a3b8; letter-spacing:.06em; text-transform:uppercase; margin-bottom:8px; }
  .tab-btn { padding:5px 14px; border-radius:6px; font-size:12px; font-weight:500; cursor:pointer; border:1px solid #e2e8f0; color:#64748b; background:#fff; }
  .gv { display:none; }
  #r-aov:checked ~ .gv-aov, #r-ff:checked ~ .gv-ff { display:block; }
  #r-aov:checked ~ .tabs .t-aov, #r-ff:checked ~ .tabs .t-ff { background:#1d4ed8; color:#fff; border-color:#1d4ed8; }

  /* 響應式網格 */
  .g-kpi      { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:14px; }
  .g-summary  { display:grid; grid-template-columns:90px 1fr; gap:12px; margin-bottom:14px; align-items:stretch; }
  .g-channels { display:grid; grid-template-columns:repeat(3,1fr); gap:10px; margin-bottom:14px; }
  .g-charts   { display:grid; grid-template-columns:3fr 2fr; gap:10px; margin-bottom:14px; }
  .g-wc       { display:grid; grid-template-columns:3fr 2fr; gap:10px; margin-bottom:16px; }

  @media (max-width:640px) {
    body { padding:10px 12px !important; }
    .card { padding:10px 12px; }
    .stat-num { font-size:1.2rem; }
    .tab-btn  { font-size:11px; padding:5px 10px; }
    .g-kpi      { grid-template-columns:repeat(2,1fr); gap:8px; }
    .g-summary  { grid-template-columns:1fr; }
    .g-channels { grid-template-columns:1fr; }
    .g-charts   { grid-template-columns:1fr; }
    .g-wc       { grid-template-columns:1fr; }
  }
"""

    parts = [
        '<!DOCTYPE html>\n<html lang="zh-TW">\n<head>\n',
        '<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width,initial-scale=1">\n',
        '<title>Garena 台灣玩家聲音儀表板</title>\n',
        '<style>', css, '</style>\n',
        '</head>\n<body style="min-height:100vh;padding:16px 24px;box-sizing:border-box;">\n',
        # Header
        '<div style="margin-bottom:14px;padding-bottom:10px;border-bottom:1px solid #e2e8f0;">',
        '<div style="font-size:10px;font-weight:600;color:#2563eb;letter-spacing:.08em;text-transform:uppercase;margin-bottom:2px;">Garena 台灣 · 玩家聲音儀表板</div>',
        '<div style="display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;">',
        '<h1 style="font-size:18px;font-weight:800;color:#0f172a;margin:0;">週間回饋總覽</h1>',
        '<span style="font-size:12px;color:#94a3b8;">', date_range, ' · 更新：', generated_at, '</span>',
        '</div></div>\n',
        # CSS-only tabs（傳說對決預設選中）
        '<input type="radio" name="game" id="r-aov" checked style="display:none">',
        '<input type="radio" name="game" id="r-ff" style="display:none">',
        '<div class="tabs" style="display:flex;gap:6px;margin-bottom:14px;">',
        '<label for="r-aov" class="tab-btn t-aov">⚔️ 傳說對決</label>',
        '<label for="r-ff" class="tab-btn t-ff">🔥 Free Fire</label>',
        '</div>\n',
        # Game views
        '<div class="gv gv-aov">', view_aov, '</div>\n',
        '<div class="gv gv-ff">', view_ff, '</div>\n',
        # Footer
        '<div style="text-align:center;font-size:10px;color:#cbd5e1;padding-bottom:12px;">',
        'Garena 台灣玩家聲音儀表板 · 資料來自 Google Play / App Store / PTT · Powered by Claude AI',
        '</div>\n',
        '</body>\n</html>\n',
    ]
    return "".join(parts)


def build_dashboard(open_browser=True):
    data = load_data(DAYS_LOOKBACK)

    data_ff  = [d for d in data if d.get("game") == "freefire"]
    data_aov = [d for d in data if d.get("game") == "aov"]

    agg_all  = aggregate(data,     DAYS_LOOKBACK)
    agg_ff   = aggregate(data_ff,  DAYS_LOOKBACK)
    agg_aov  = aggregate(data_aov, DAYS_LOOKBACK)
    channels = channel_breakdown(data)

    today      = datetime.utcnow().date()
    week_start = today - timedelta(days=DAYS_LOOKBACK - 1)
    date_range = week_start.strftime("%Y/%m/%d") + " – " + today.strftime("%Y/%m/%d")

    # AI 摘要（兩個遊戲分頁各呼叫一次）
    print("🤖 正在呼叫 Claude 產生本週洞察摘要…")
    summary_aov = generate_ai_summary(data_aov, "傳說對決")
    summary_ff  = generate_ai_summary(data_ff,  "Free Fire")
    print("✅ AI 摘要完成")

    html = generate_html(agg_all, agg_ff, agg_aov, channels, date_range,
                         summary_ff=summary_ff, summary_aov=summary_aov)
    OUTPUT_PATH.write_text(html, encoding="utf-8")

    ff_n  = len(data_ff)
    aov_n = len(data_aov)
    print("✅ 儀表板已生成：" + str(OUTPUT_PATH))
    print("   " + date_range + "  共 " + str(agg_all["total"]) + " 則（FF:" + str(ff_n) + " 傳說:" + str(aov_n) + "）")

    try:
        from deploy import deploy
        deploy()
    except Exception as e:
        print("（GitHub Pages 部署跳過：" + str(e) + "）")

    if open_browser:
        _open_in_browser(OUTPUT_PATH)

    return OUTPUT_PATH


def _open_in_browser(path):
    try:
        subprocess.Popen(["open", str(path)])
    except Exception as e:
        print("（無法自動開啟瀏覽器：" + str(e) + "）\n請手動開啟：" + str(path))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成 Garena 玩家聲音儀表板")
    parser.add_argument("--no-open", action="store_true", help="只生成 HTML，不開啟瀏覽器")
    args = parser.parse_args()
    build_dashboard(open_browser=not args.no_open)
