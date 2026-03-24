"""
dashboard_generator.py — 從 state.db 讀取過去 7 天資料，生成靜態 HTML 儀表板

用法：
  python3 dashboard_generator.py          # 生成 dashboard.html 並在瀏覽器開啟
  python3 dashboard_generator.py --no-open  # 只生成，不開啟瀏覽器
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

DB_PATH        = Path(__file__).parent / "state.db"
OUTPUT_PATH    = Path(__file__).parent / "dashboard.html"
DAYS_LOOKBACK  = 7

SOURCE_LABELS = {
    "appstore_freefire":   "App Store・Free Fire",
    "appstore_aov":        "App Store・傳說對決",
    "googleplay_freefire": "Google Play・Free Fire",
    "googleplay_aov":      "Google Play・傳說對決",
    "ptt":                 "PTT",
    "dcard":               "Dcard",
}
CATEGORY_COLORS = {
    "客服問題": "#F97316",
    "活動反應": "#3B82F6",
    "Bug回報":  "#EF4444",
    "付費體驗": "#EAB308",
    "遊戲體驗": "#22C55E",
    "其他":     "#8B5CF6",
}
SENTIMENT_COLORS = {
    "正面": "#22C55E",
    "負面": "#EF4444",
    "中性": "#94A3B8",
}


# ── 資料讀取 ─────────────────────────────────────────────────

def load_data(days: int = DAYS_LOOKBACK) -> list[dict]:
    if not DB_PATH.exists():
        return []
    since = (datetime.utcnow() - timedelta(days=days)).isoformat()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM analyses WHERE analyzed_at >= ? ORDER BY analyzed_at DESC",
            (since,)
        ).fetchall()
    result = []
    for row in rows:
        d = dict(row)
        try:
            d["keywords"] = json.loads(d["keywords"]) if d["keywords"] else []
        except Exception:
            d["keywords"] = []
        result.append(d)
    return result


# ── 資料彙整 ─────────────────────────────────────────────────

def aggregate(data: list[dict]) -> dict:
    total = len(data)

    # 分類 & 情緒統計
    by_category  = Counter(d.get("category", "其他") for d in data)
    by_sentiment = Counter(d.get("sentiment", "中性") for d in data)

    # 來源統計
    by_source = Counter(d.get("source", "") for d in data)

    # 每日趨勢（過去 7 天，以 analyzed_at 日期分組）
    daily: dict[str, int] = defaultdict(int)
    today = datetime.utcnow().date()
    for i in range(DAYS_LOOKBACK):
        day_str = (today - timedelta(days=DAYS_LOOKBACK - 1 - i)).strftime("%m/%d")
        daily[day_str] = 0
    for d in data:
        try:
            day = datetime.fromisoformat(d["analyzed_at"]).date()
            day_str = day.strftime("%m/%d")
            if day_str in daily:
                daily[day_str] += 1
        except Exception:
            pass

    # Top 10 關鍵詞
    kw_counter: Counter = Counter()
    for d in data:
        for kw in d.get("keywords", []):
            if kw:
                kw_counter[kw] += 1
    top_keywords = kw_counter.most_common(10)

    # 最新 20 筆明細
    recent = data[:20]

    # 負評最集中分類
    neg_by_cat: Counter = Counter()
    for d in data:
        if d.get("sentiment") == "負面":
            neg_by_cat[d.get("category", "其他")] += 1

    return {
        "total":         total,
        "by_category":   dict(by_category),
        "by_sentiment":  dict(by_sentiment),
        "by_source":     {SOURCE_LABELS.get(k, k): v for k, v in by_source.items()},
        "daily":         dict(daily),
        "top_keywords":  top_keywords,
        "recent":        recent,
        "neg_by_cat":    dict(neg_by_cat),
    }


# ── HTML 生成 ─────────────────────────────────────────────────

def generate_html(agg: dict, date_range: str) -> str:
    total       = agg["total"]
    neg         = agg["by_sentiment"].get("負面", 0)
    pos         = agg["by_sentiment"].get("正面", 0)
    neg_pct     = f"{neg/total*100:.0f}%" if total else "0%"
    pos_pct     = f"{pos/total*100:.0f}%" if total else "0%"

    # JSON for charts
    cat_labels  = list(CATEGORY_COLORS.keys())
    cat_vals    = [agg["by_category"].get(c, 0) for c in cat_labels]
    cat_colors  = [CATEGORY_COLORS[c] for c in cat_labels]

    sent_labels = ["正面", "負面", "中性"]
    sent_vals   = [agg["by_sentiment"].get(s, 0) for s in sent_labels]
    sent_colors = [SENTIMENT_COLORS[s] for s in sent_labels]

    daily_labels = list(agg["daily"].keys())
    daily_vals   = list(agg["daily"].values())

    src_labels   = list(agg["by_source"].keys())
    src_vals     = list(agg["by_source"].values())

    kw_labels = [k for k, _ in agg["top_keywords"]]
    kw_vals   = [v for _, v in agg["top_keywords"]]

    # 最新明細 rows（先組好，避免 f-string 嵌套問題）
    recent_count = len(agg["recent"])
    recent_rows = ""
    for d in agg["recent"]:
        cat  = d.get("category", "—")
        sent = d.get("sentiment", "—")
        src  = SOURCE_LABELS.get(d.get("source", ""), d.get("source", ""))
        summ = (d.get("summary") or d.get("body", "")[:60]).replace("<", "&lt;").replace(">", "&gt;")
        kws  = "、".join(d.get("keywords", []))
        date = d.get("analyzed_at", "")[:10]
        cat_color  = CATEGORY_COLORS.get(cat, "#8B5CF6")
        sent_color = SENTIMENT_COLORS.get(sent, "#94A3B8")
        url  = d.get("url", "#")
        recent_rows += f"""
        <tr class="border-b border-slate-700 hover:bg-slate-700/30 transition-colors">
          <td class="py-2 px-3 text-xs text-slate-400 whitespace-nowrap">{date}</td>
          <td class="py-2 px-3">
            <span class="px-2 py-0.5 rounded-full text-xs font-bold" style="background:{cat_color}22;color:{cat_color}">{cat}</span>
          </td>
          <td class="py-2 px-3">
            <span class="px-2 py-0.5 rounded-full text-xs font-bold" style="background:{sent_color}22;color:{sent_color}">{sent}</span>
          </td>
          <td class="py-2 px-3 text-sm text-slate-200 max-w-xs truncate">
            <a href="{url}" target="_blank" class="hover:text-blue-400 transition-colors">{summ}</a>
          </td>
          <td class="py-2 px-3 text-xs text-slate-400">{kws}</td>
          <td class="py-2 px-3 text-xs text-slate-500 whitespace-nowrap">{src}</td>
        </tr>"""

    # 關鍵詞 & 來源圖表 JS（避免 f-string 嵌套）
    if kw_labels:
        kw_chart_js = (
            "new Chart(document.getElementById('kwChart'), {"
            "  type: 'bar',"
            "  data: {"
            f"    labels: {json.dumps(kw_labels, ensure_ascii=False)},"
            f"    datasets: [{{ data: {json.dumps(kw_vals)}, backgroundColor: '#8b5cf6', borderRadius: 4 }}]"
            "  },"
            "  options: { indexAxis: 'y', plugins: { legend: { display: false } },"
            "    scales: { x: { ticks: { color: '#64748b' }, grid: { color: '#334155' } },"
            "              y: { ticks: { color: '#94a3b8', font: { size: 11 } }, grid: { display: false } } } }"
            "});"
        )
    else:
        kw_chart_js = ""

    if src_labels:
        src_chart_js = (
            "new Chart(document.getElementById('srcChart'), {"
            "  type: 'bar',"
            "  data: {"
            f"    labels: {json.dumps(src_labels, ensure_ascii=False)},"
            f"    datasets: [{{ data: {json.dumps(src_vals)}, backgroundColor: '#06b6d4', borderRadius: 4 }}]"
            "  },"
            "  options: { plugins: { legend: { display: false } },"
            "    scales: { x: { ticks: { color: '#64748b', font: { size: 10 }, maxRotation: 30 }, grid: { display: false } },"
            "              y: { ticks: { color: '#64748b' }, grid: { color: '#334155' } } } }"
            "});"
        )
    else:
        src_chart_js = ""

    if agg["recent"]:
        recent_table = (
            '<div class="overflow-x-auto"><table class="w-full text-left">'
            '<thead><tr class="border-b border-slate-600">'
            '<th class="py-2 px-3 text-xs text-slate-500 font-medium">日期</th>'
            '<th class="py-2 px-3 text-xs text-slate-500 font-medium">分類</th>'
            '<th class="py-2 px-3 text-xs text-slate-500 font-medium">情緒</th>'
            '<th class="py-2 px-3 text-xs text-slate-500 font-medium">摘要</th>'
            '<th class="py-2 px-3 text-xs text-slate-500 font-medium">關鍵詞</th>'
            '<th class="py-2 px-3 text-xs text-slate-500 font-medium">來源</th>'
            '</tr></thead>'
            '<tbody>' + recent_rows + '</tbody>'
            '</table></div>'
        )
    else:
        recent_table = '<div class="text-slate-500 text-sm">本週無資料</div>'

    generated_at = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")

    return f"""<!DOCTYPE html>
<html lang="zh-TW">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Garena 台灣玩家聲音儀表板</title>
<script src="https://cdn.tailwindcss.com"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  body {{ background:#0f172a; color:#e2e8f0; font-family:'Helvetica Neue',Arial,sans-serif; }}
  .card {{ background:#1e293b; border-radius:12px; padding:20px; }}
  .stat-num {{ font-size:2rem; font-weight:800; line-height:1; }}
  canvas {{ max-height:220px; }}
</style>
</head>
<body class="min-h-screen p-6">

<!-- Header -->
<div class="mb-6">
  <div class="text-xs tracking-widest text-blue-400 uppercase mb-1">Garena 台灣玩家聲音儀表板</div>
  <h1 class="text-2xl font-bold text-white">週間回饋總覽</h1>
  <div class="text-sm text-slate-400 mt-1">資料區間：{date_range}　　最後更新：{generated_at}</div>
</div>

<!-- KPI 卡片 -->
<div class="grid grid-cols-2 md:grid-cols-4 gap-4 mb-6">
  <div class="card">
    <div class="text-xs text-slate-400 mb-2">本週總回饋</div>
    <div class="stat-num text-white">{total}</div>
    <div class="text-xs text-slate-500 mt-1">則</div>
  </div>
  <div class="card">
    <div class="text-xs text-slate-400 mb-2">負面比例</div>
    <div class="stat-num text-red-400">{neg_pct}</div>
    <div class="text-xs text-slate-500 mt-1">{neg} 則</div>
  </div>
  <div class="card">
    <div class="text-xs text-slate-400 mb-2">正面比例</div>
    <div class="stat-num text-green-400">{pos_pct}</div>
    <div class="text-xs text-slate-500 mt-1">{pos} 則</div>
  </div>
  <div class="card">
    <div class="text-xs text-slate-400 mb-2">活躍來源</div>
    <div class="stat-num text-blue-400">{len([v for v in agg["by_source"].values() if v > 0])}</div>
    <div class="text-xs text-slate-500 mt-1">個平台</div>
  </div>
</div>

<!-- 圖表區 -->
<div class="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-4 gap-4 mb-6">

  <!-- 每日趨勢 -->
  <div class="card xl:col-span-2">
    <div class="text-xs text-slate-400 uppercase tracking-wider mb-3">每日回饋量趨勢</div>
    <canvas id="trendChart"></canvas>
  </div>

  <!-- 分類分布 -->
  <div class="card">
    <div class="text-xs text-slate-400 uppercase tracking-wider mb-3">回饋分類</div>
    <canvas id="catChart"></canvas>
  </div>

  <!-- 情緒分布 -->
  <div class="card">
    <div class="text-xs text-slate-400 uppercase tracking-wider mb-3">情緒分布</div>
    <canvas id="sentChart"></canvas>
  </div>

</div>

<!-- 第二行圖表 -->
<div class="grid grid-cols-1 md:grid-cols-2 gap-4 mb-6">

  <!-- Top 關鍵詞 -->
  <div class="card">
    <div class="text-xs text-slate-400 uppercase tracking-wider mb-3">本週熱門關鍵詞 Top {len(kw_labels)}</div>
    {"<canvas id='kwChart'></canvas>" if kw_labels else '<div class="text-slate-500 text-sm">本週無關鍵詞資料</div>'}
  </div>

  <!-- 來源分布 -->
  <div class="card">
    <div class="text-xs text-slate-400 uppercase tracking-wider mb-3">各平台回饋量</div>
    {"<canvas id='srcChart'></canvas>" if src_labels else '<div class="text-slate-500 text-sm">本週無資料</div>'}
  </div>

</div>

<!-- 最新明細 -->
<div class="card mb-6">
  <div class="text-xs text-slate-400 uppercase tracking-wider mb-3">最新回饋明細（最近 {recent_count} 則）</div>
  {recent_table}
</div>

<!-- Footer -->
<div class="text-center text-xs text-slate-600 pb-4">
  Garena 台灣玩家聲音儀表板 · 資料來自 Google Play / App Store / PTT · Powered by Claude AI
</div>

<script>
const chartDefaults = {{
  plugins: {{ legend: {{ labels: {{ color: '#94a3b8', font: {{ size: 11 }} }} }} }},
  scales: {{
    x: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#1e293b' }} }},
    y: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#334155' }} }}
  }}
}};

// 每日趨勢
new Chart(document.getElementById('trendChart'), {{
  type: 'line',
  data: {{
    labels: {json.dumps(daily_labels, ensure_ascii=False)},
    datasets: [{{
      label: '回饋則數',
      data: {json.dumps(daily_vals)},
      borderColor: '#3b82f6',
      backgroundColor: 'rgba(59,130,246,0.15)',
      borderWidth: 2,
      fill: true,
      tension: 0.4,
      pointBackgroundColor: '#3b82f6',
      pointRadius: 4,
    }}]
  }},
  options: {{ ...chartDefaults, plugins: {{ ...chartDefaults.plugins, legend: {{ display: false }} }} }}
}});

// 分類 (橫條)
new Chart(document.getElementById('catChart'), {{
  type: 'bar',
  data: {{
    labels: {json.dumps(cat_labels, ensure_ascii=False)},
    datasets: [{{
      data: {json.dumps(cat_vals)},
      backgroundColor: {json.dumps(cat_colors)},
      borderRadius: 4,
    }}]
  }},
  options: {{
    indexAxis: 'y',
    plugins: {{ legend: {{ display: false }} }},
    scales: {{
      x: {{ ticks: {{ color: '#64748b' }}, grid: {{ color: '#334155' }} }},
      y: {{ ticks: {{ color: '#94a3b8', font: {{ size: 11 }} }}, grid: {{ display: false }} }}
    }}
  }}
}});

// 情緒 (donut)
new Chart(document.getElementById('sentChart'), {{
  type: 'doughnut',
  data: {{
    labels: {json.dumps(sent_labels, ensure_ascii=False)},
    datasets: [{{
      data: {json.dumps(sent_vals)},
      backgroundColor: {json.dumps(sent_colors)},
      borderWidth: 0,
    }}]
  }},
  options: {{
    cutout: '60%',
    plugins: {{ legend: {{ position: 'bottom', labels: {{ color: '#94a3b8', font: {{ size: 11 }}, padding: 12 }} }} }}
  }}
}});

{kw_chart_js}
{src_chart_js}
</script>
</body>
</html>"""


# ── 主流程 ────────────────────────────────────────────────────

def build_dashboard(open_browser: bool = True) -> Path:
    data = load_data(DAYS_LOOKBACK)
    agg  = aggregate(data)

    today = datetime.utcnow().date()
    week_start = today - timedelta(days=DAYS_LOOKBACK - 1)
    date_range = f"{week_start.strftime('%Y/%m/%d')} – {today.strftime('%Y/%m/%d')}"

    html = generate_html(agg, date_range)
    OUTPUT_PATH.write_text(html, encoding="utf-8")

    print(f"✅ 儀表板已生成：{OUTPUT_PATH}")
    print(f"   資料區間：{date_range}　共 {agg['total']} 則")

    # 自動部署到 GitHub Pages（若已設定）
    try:
        from deploy import deploy
        deploy()
    except Exception as e:
        print(f"（GitHub Pages 部署跳過：{e}）")

    if open_browser:
        _open_in_browser(OUTPUT_PATH)

    return OUTPUT_PATH


def _open_in_browser(path: Path) -> None:
    try:
        subprocess.Popen(["open", str(path)])
    except Exception as e:
        print(f"（無法自動開啟瀏覽器：{e}）\n請手動開啟：{path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="生成 Garena 玩家聲音儀表板")
    parser.add_argument("--no-open", action="store_true", help="只生成 HTML，不開啟瀏覽器")
    args = parser.parse_args()
    build_dashboard(open_browser=not args.no_open)
