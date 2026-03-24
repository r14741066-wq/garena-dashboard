"""
scrapers/ptt.py — 抓取 PTT 遊戲相關討論串（Guest 模式）
目標看板：C_Chat / Gossiping（熱門遊戲討論區）
以關鍵詞篩選與 Free Fire / 傳說對決相關的文章。

PTT Guest 模式：先 POST /login 取得 over18 session cookie，
再帶著 cookie 正常瀏覽看板頁面。
"""
import logging
import time
from datetime import datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from scrapers.base import BaseScraper, RawItem

logger = logging.getLogger(__name__)

PTT_BASE = "https://www.ptt.cc"
BOARDS = ["C_Chat", "GameDesign"]
KEYWORDS = ["freefire", "free fire", "傳說對決", "aov", "garena", "荒野行動", "free_fire"]

# 完整瀏覽器 Headers，降低被 reset 的機率
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/605.1.15 (KHTML, like Gecko) "
        "Version/17.4 Safari/605.1.15"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://www.ptt.cc/bbs/index.html",
}


class PTTScraper(BaseScraper):
    def __init__(self, max_items: int = 50):
        self.max_items = max_items

    def _fetch(self, since: datetime) -> list[RawItem]:
        all_items: list[RawItem] = []
        since_naive = since.replace(tzinfo=None) if since.tzinfo else since

        # 建立帶 over18 cookie 的 session
        client = self._make_client()

        try:
            for board in BOARDS:
                try:
                    items = self._fetch_board(client, board, since_naive)
                    all_items.extend(items)
                except Exception as e:
                    logger.warning(f"[PTT] {board} 抓取失敗：{e}")

                if len(all_items) >= self.max_items:
                    break
                time.sleep(1.5)
        finally:
            client.close()

        return all_items[:self.max_items]

    # ── Session 建立（Guest 登入取得 over18 cookie） ──────────────

    def _make_client(self) -> httpx.Client:
        """建立已過 over18 驗證的 httpx.Client。"""
        client = httpx.Client(
            headers=HEADERS,
            cookies={"over18": "1"},
            timeout=20,
            follow_redirects=True,
        )
        # 先訪問首頁讓 PTT 設置 session cookie
        try:
            resp = client.get(f"{PTT_BASE}/bbs/index.html")
            # 如果被導向 over18 確認頁，自動 POST 確認
            if "over18" in resp.url.path or "over18" in resp.text:
                client.post(
                    f"{PTT_BASE}/ask/over18",
                    data={"from": "/bbs/C_Chat/index.html", "yes": "yes"},
                )
                logger.debug("[PTT] 已完成 over18 確認")
        except Exception as e:
            logger.debug(f"[PTT] 建立 session 時發生非嚴重錯誤（繼續）：{e}")
        return client

    # ── 看板爬取 ──────────────────────────────────────────────────

    def _fetch_board(self, client: httpx.Client, board: str, since: datetime) -> list[RawItem]:
        items = []
        url = f"{PTT_BASE}/bbs/{board}/index.html"
        pages_checked = 0
        max_pages = 20

        while url and pages_checked < max_pages:
            resp = self._get_with_retry(client, url)
            if resp is None:
                break

            soup = BeautifulSoup(resp.text, "lxml")
            articles = soup.select("div.r-ent")
            reached_before_since = False

            for article in articles:
                try:
                    title_tag = article.select_one("div.title a")
                    if not title_tag:
                        continue
                    title = title_tag.get_text(strip=True)
                    article_url = PTT_BASE + title_tag["href"]

                    if not self._matches_keywords(title):
                        continue

                    date_tag = article.select_one("div.date")
                    pub_date = None
                    if date_tag:
                        pub_date = self._parse_date(date_tag.get_text(strip=True))
                        if pub_date and pub_date < since:
                            reached_before_since = True
                            continue

                    time.sleep(0.8)
                    body = self._fetch_article(client, article_url)
                    if not body:
                        continue

                    native_id = article_url.split("/bbs/")[-1].replace(".html", "").replace("/", "_")
                    author_tag = article.select_one("div.author")

                    items.append(RawItem(
                        source="ptt",
                        native_id=native_id,
                        title=title,
                        body=body,
                        rating=None,
                        author=author_tag.get_text(strip=True) if author_tag else "",
                        url=article_url,
                        published_at=pub_date or datetime.utcnow(),
                    ))

                    if len(items) >= self.max_items:
                        logger.info(f"[PTT] {board}：{len(items)} 篇（達上限）")
                        return items

                except Exception as e:
                    logger.debug(f"[PTT] 解析文章失敗：{e}")
                    continue

            if reached_before_since:
                break

            prev_tag = None
            for btn in soup.select("a.btn.wide"):
                if "上頁" in btn.get_text():
                    prev_tag = btn
                    break

            if prev_tag and prev_tag.get("href"):
                url = PTT_BASE + prev_tag["href"]
            else:
                break

            pages_checked += 1
            time.sleep(1.0)

        logger.info(f"[PTT] {board}：找到 {len(items)} 篇相關文章")
        return items

    def _get_with_retry(self, client: httpx.Client, url: str, retries: int = 3) -> Optional[httpx.Response]:
        """帶重試的 GET，處理 Connection reset。"""
        for attempt in range(retries):
            try:
                resp = client.get(url)
                resp.raise_for_status()
                return resp
            except (httpx.RemoteProtocolError, httpx.ConnectError, httpx.ReadError) as e:
                wait = 2 ** attempt  # 指數退避：1s, 2s, 4s
                logger.warning(f"[PTT] 請求失敗（第 {attempt+1}/{retries} 次）{url}：{e}，等待 {wait}s")
                time.sleep(wait)
            except httpx.HTTPStatusError as e:
                logger.warning(f"[PTT] HTTP 錯誤 {url}：{e}")
                return None
            except Exception as e:
                logger.warning(f"[PTT] 未預期錯誤 {url}：{e}")
                return None
        logger.warning(f"[PTT] 達到最大重試次數，跳過：{url}")
        return None

    def _fetch_article(self, client: httpx.Client, url: str) -> str:
        """抓取文章內文，去除引用與推文。"""
        resp = self._get_with_retry(client, url, retries=2)
        if not resp:
            return ""
        try:
            soup = BeautifulSoup(resp.text, "lxml")
            main = soup.find("div", id="main-content")
            if not main:
                return ""

            for tag in main.select("div.article-metaline, div.article-metaline-right, div.push"):
                tag.decompose()

            text = main.get_text(separator="\n")
            lines = [
                line for line in text.splitlines()
                if line.strip()
                and not line.strip().startswith(">")
                and not line.strip().startswith("※")
            ]
            return "\n".join(lines).strip()
        except Exception as e:
            logger.debug(f"[PTT] 解析內文失敗 {url}：{e}")
            return ""

    @staticmethod
    def _matches_keywords(title: str) -> bool:
        title_lower = title.lower()
        return any(kw in title_lower for kw in KEYWORDS)

    @staticmethod
    def _parse_date(date_str: str) -> Optional[datetime]:
        """將 PTT 日期格式（如 ' 3/24'）轉換為 datetime（使用當前年份）。"""
        try:
            parts = date_str.strip().split("/")
            if len(parts) == 2:
                month, day = int(parts[0]), int(parts[1])
                year = datetime.utcnow().year
                return datetime(year, month, day)
        except Exception:
            pass
        return None
