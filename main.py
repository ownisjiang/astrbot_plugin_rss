"""
AstrBot RSS 订阅插件
========================
功能特色:
  - 订阅/取消订阅 RSS/Atom 源
  - 定时后台轮询新条目，自动去重
  - 新内容自动推送到订阅者的聊天窗口
  - 支持按源设置检查间隔
  - 数据持久化存储

命令:
  /rss add <url>              订阅 RSS 源
  /rss remove <id>            取消订阅
  /rss list                   列出所有订阅
  /rss set <id> interval <N>  设置检查间隔(分钟)
  /rss help                   显示帮助
"""

import asyncio
import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import feedparser
import httpx

from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star, register
from astrbot.api import logger
from astrbot.api.message_components import Plain, Image


# ── 持久化存储 ──────────────────────────────────────────────


class SubscriptionStore:
    """线程安全的订阅数据存储，使用 JSON 文件持久化"""

    def __init__(self, data_dir: str):
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._file = self.data_dir / "rss_subscriptions.json"
        self._lock = asyncio.Lock()
        self._data: dict = self._load()

    # ── 内部读写 ──

    def _load(self) -> dict:
        if not self._file.exists():
            return {"feeds": [], "entries_seen": {}}
        try:
            with open(self._file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"读取订阅数据失败: {e}")
            return {"feeds": [], "entries_seen": {}}

    async def _save(self):
        async with self._lock:
            tmp = self._file.with_suffix(".tmp")
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(self._data, f, ensure_ascii=False, indent=2)
                tmp.replace(self._file)
            except OSError as e:
                logger.error(f"保存订阅数据失败: {e}")

    @staticmethod
    def _generate_id() -> str:
        raw = f"{time.time_ns()}"
        return hashlib.md5(raw.encode()).hexdigest()[:8]

    # ── Feed 管理 ──

    def get_feeds(self) -> list:
        return self._data.get("feeds", [])

    async def add_feed(
        self, url: str, title: str, subtitle: str, default_interval: int = 30
    ) -> tuple[bool, str]:
        async with self._lock:
            feeds = self._data["feeds"]
            if any(f["url"] == url for f in feeds):
                return False, ""

            feed_id = self._generate_id()
            feeds.append(
                {
                    "id": feed_id,
                    "url": url,
                    "title": title,
                    "subtitle": subtitle,
                    "interval": default_interval,
                    "last_entry_id": "",
                    "subscribers": [],
                    "added_at": datetime.now(timezone.utc).isoformat(),
                    "last_check": None,
                    "failed_count": 0,
                }
            )
            await self._save()
        return True, feed_id

    async def remove_feed(self, feed_id: str) -> bool:
        async with self._lock:
            feeds = self._data["feeds"]
            before = len(feeds)
            self._data["feeds"] = [f for f in feeds if f["id"] != feed_id]
            if len(self._data["feeds"]) < before:
                await self._save()
                return True
        return False

    async def update_feed(self, feed_id: str, **kwargs) -> bool:
        async with self._lock:
            for feed in self._data["feeds"]:
                if feed["id"] == feed_id:
                    feed.update(kwargs)
                    await self._save()
                    return True
        return False

    # ── 条目去重 ──

    def get_seen_entries(self) -> dict:
        return self._data.get("entries_seen", {})

    async def mark_entries_seen(self, entry_ids: list):
        async with self._lock:
            seen = self._data["entries_seen"]
            for eid in entry_ids:
                seen[eid] = datetime.now(timezone.utc).isoformat()
            await self._save()

    # ── 订阅者管理 ──

    async def add_subscriber(
        self, feed_url: str, unified_origin: str, platform: str, sender_name: str
    ) -> bool:
        async with self._lock:
            for feed in self._data["feeds"]:
                if feed["url"] == feed_url:
                    existing = [
                        s
                        for s in feed.get("subscribers", [])
                        if s["unified_origin"] == unified_origin
                    ]
                    if not existing:
                        feed.setdefault("subscribers", []).append(
                            {
                                "unified_origin": unified_origin,
                                "platform": platform,
                                "sender_name": sender_name,
                            }
                        )
                        await self._save()
                        return True
                    return False
        return False

    def get_subscribers_for_feed(self, feed_url: str) -> list:
        for feed in self._data["feeds"]:
            if feed["url"] == feed_url:
                return feed.get("subscribers", [])
        return []


# ── 插件主类 ──────────────────────────────────────────────


@register(
    "astrbot_plugin_rss",
    "ownisjiang",
    "RSS 订阅插件 - 订阅你喜欢的博客和新闻源",
    "1.0.0",
)
class RssPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config or {}
        self._running = False
        self._poll_task: Optional[asyncio.Task] = None

        # 持久化数据放在 AstrBot data 目录下
        data_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "..",
            "..",
            "..",
            "data",
            "rss_plugin",
        )
        self.store = SubscriptionStore(data_dir)

    # ── 生命周期 ──

    async def initialize(self):
        self._running = True
        self._poll_task = asyncio.create_task(self._poll_loop())
        logger.info("RSS 插件已初始化，后台轮询已启动")

    async def terminate(self):
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        logger.info("RSS 插件已停止")

    # ── 命令: /rss ──

    @filter.command("rss")
    async def rss(self, event: AstrMessageEvent, action: str = None):
        """RSS 订阅管理主命令"""
        if not action:
            yield event.plain_result("📡 RSS 订阅管理器\n输入 /rss help 查看帮助")
            return

        action = action.lower()

        if action in ("help", "h"):
            yield self._cmd_help(event)

        elif action in ("add", "a"):
            # 从消息体中提取 URL（第二个参数）
            parts = event.message_str.strip().split(maxsplit=2)
            if len(parts) < 3:
                yield event.plain_result(
                    "❌ 用法: /rss add <url>\n例如: /rss add https://example.com/rss"
                )
                return
            url = parts[2].strip("\"'")  # 去除可能的引号
            async for result in self._cmd_add(event, url):
                yield result

        elif action in ("remove", "rm", "delete", "del"):
            parts = event.message_str.strip().split(maxsplit=2)
            if len(parts) < 3:
                yield event.plain_result(
                    "❌ 用法: /rss remove <id>\n例如: /rss remove abc12345"
                )
                return
            feed_id = parts[2]
            async for result in self._cmd_remove(event, feed_id):
                yield result

        elif action in ("list", "ls"):
            async for result in self._cmd_list(event):
                yield result

        elif action == "set":
            parts = event.message_str.strip().split()
            # /rss set <id> interval <N>
            if len(parts) < 5 or parts[3] != "interval":
                yield event.plain_result(
                    "❌ 用法: /rss set <id> interval <分钟>\n"
                    "例如: /rss set abc12345 interval 15"
                )
                return
            feed_id = parts[2]
            try:
                interval = int(parts[4])
            except ValueError:
                yield event.plain_result("❌ 间隔时间必须是数字（分钟）")
                return
            interval = max(5, min(interval, 1440))

            ok = await self.store.update_feed(feed_id, interval=interval)
            if ok:
                yield event.plain_result(
                    f"✅ 已更新检查间隔为 {interval} 分钟 (ID: {feed_id})"
                )
            else:
                yield event.plain_result(
                    f"❌ 未找到 ID 为 {feed_id} 的订阅，使用 /rss list 查看"
                )

        else:
            yield event.plain_result(
                f"❌ 未知操作: {action}\n输入 /rss help 查看可用命令"
            )

    # ── 子命令实现 ──

    def _cmd_help(self, event: AstrMessageEvent):
        return event.plain_result(
            "📡 RSS 订阅插件帮助\n"
            "━━━━━━━━━━━━━━━━\n"
            "命令:\n"
            "  /rss add <url>               订阅 RSS/Atom 源\n"
            "  /rss remove <id>             取消订阅\n"
            "  /rss list                    查看所有订阅\n"
            "  /rss set <id> interval <N>   设置检查间隔(分钟，5~1440)\n"
            "  /rss help                    显示此帮助\n"
            "━━━━━━━━━━━━━━━━\n"
            "插件会自动检查更新并推送到你的聊天 ✅"
        )

    async def _cmd_add(self, event: AstrMessageEvent, url: str):
        yield event.plain_result(f"🔍 正在获取: {url}")

        feed_info = await self._fetch_feed_info(url)
        if feed_info is None:
            yield event.plain_result(
                "❌ 无法解析该地址，请确认是否为有效的 RSS/Atom 源"
            )
            return

        title = feed_info.get("title", url)
        subtitle = feed_info.get("subtitle", "")
        default_interval = self.config.get("default_interval", 30)

        ok, feed_id = await self.store.add_feed(url, title, subtitle, default_interval)
        if not ok:
            yield event.plain_result("⚠️ 该地址已经订阅过了")
            return

        # 将当前会话注册为此源的订阅者
        await self.store.add_subscriber(
            url,
            event.unified_msg_origin,
            event.get_platform_name(),
            event.get_sender_name(),
        )

        yield event.plain_result(
            f"✅ 订阅成功！\n"
            f"📰 {title}\n"
            f"🆔 ID: {feed_id}\n"
            f"⏱ 检查间隔: {default_interval} 分钟\n"
            f"📌 新内容将自动推送至此会话"
        )

    async def _cmd_remove(self, event: AstrMessageEvent, feed_id: str):
        ok = await self.store.remove_feed(feed_id)
        if ok:
            yield event.plain_result(f"✅ 已取消订阅 (ID: {feed_id})")
        else:
            yield event.plain_result(
                f"❌ 未找到 ID 为 {feed_id} 的订阅\n使用 /rss list 查看所有订阅"
            )

    async def _cmd_list(self, event: AstrMessageEvent):
        feeds = self.store.get_feeds()
        if not feeds:
            yield event.plain_result("📭 暂无订阅，使用 /rss add <url> 添加")
            return

        lines = ["📡 RSS 订阅列表:\n"]
        for feed in feeds:
            sub_count = len(feed.get("subscribers", []))
            title = feed.get("title", feed["url"])
            url_short = feed["url"][:60]
            if len(feed["url"]) > 60:
                url_short += "..."
            interval = feed.get("interval", 30)
            lines.append(
                f"  🆔 {feed['id']} — {title}\n"
                f"     📎 {url_short}\n"
                f"     ⏱ {interval}分钟 | 👥 {sub_count}人订阅\n"
            )
        yield event.plain_result("".join(lines))

    # ── 后台轮询 ──────────────────────────────────────────

    async def _poll_loop(self):
        """每隔 60 秒检查一次各源是否该轮询了"""
        while self._running:
            try:
                feeds = self.store.get_feeds()
                now = datetime.now(timezone.utc)

                for feed in feeds:
                    if not self._running:
                        break

                    interval = feed.get("interval", 30)
                    last_check = feed.get("last_check")

                    # 判断是否到了检查时间
                    if last_check:
                        try:
                            last = datetime.fromisoformat(last_check)
                            elapsed_min = (now - last).total_seconds() / 60
                        except (ValueError, TypeError):
                            elapsed_min = interval + 1  # 格式异常则立即检查
                        if elapsed_min < interval:
                            continue

                    await self._check_single_feed(feed)

                await asyncio.sleep(60)

            except asyncio.CancelledError:
                logger.info("RSS 轮询任务被取消")
                break
            except Exception as e:
                logger.error(f"轮询循环异常: {e}")
                await asyncio.sleep(60)

    async def _check_single_feed(self, feed: dict):
        """检查单个 RSS 源的新条目并推送"""
        url = feed["url"]
        title = feed.get("title", url)
        logger.info(f"检查 RSS 更新: {title}")

        try:
            new_entries = await self._fetch_new_entries(feed)
        except Exception as e:
            logger.error(f"获取 RSS 条目失败 {url}: {e}")
            await self.store.update_feed(
                feed["id"],
                last_check=datetime.now(timezone.utc).isoformat(),
                failed_count=feed.get("failed_count", 0) + 1,
            )
            return

        if new_entries:
            logger.info(f"{title}: 发现 {len(new_entries)} 条新内容")
            await self._push_entries(url, new_entries, feed)

            entry_ids = [e["id"] for e in new_entries]
            await self.store.mark_entries_seen(entry_ids)
            await self.store.update_feed(feed["id"], last_entry_id=entry_ids[0])

        await self.store.update_feed(
            feed["id"],
            last_check=datetime.now(timezone.utc).isoformat(),
            failed_count=0,
        )

    # ── RSS 获取与解析 ──

    async def _fetch_feed_info(self, url: str) -> Optional[dict]:
        """获取 RSS 源的标题等信息"""
        content = await self._fetch_url(url)
        if content is None:
            return None

        try:
            parsed = feedparser.parse(content)
            if parsed.bozo and not parsed.entries:
                logger.warning(f"解析 RSS 失败: {parsed.bozo_exception}")
                return None
            return {
                "title": parsed.feed.get("title", ""),
                "subtitle": parsed.feed.get("subtitle", ""),
                "link": parsed.feed.get("link", ""),
            }
        except Exception as e:
            logger.error(f"解析 RSS 内容异常: {e}")
            return None

    async def _fetch_new_entries(self, feed: dict) -> list:
        """获取指定源中未推送过的新条目"""
        url = feed["url"]
        content = await self._fetch_url(url)
        if content is None:
            return []

        parsed = feedparser.parse(content)
        if not parsed.entries:
            return []

        seen = self.store.get_seen_entries()
        max_push = self.config.get("max_entries_per_push", 5)

        new_entries = []
        for entry in parsed.entries:
            entry_id = entry.get("id") or entry.get("link") or ""
            if not entry_id:
                continue
            if entry_id in seen:
                continue

            new_entries.append(
                {
                    "id": entry_id,
                    "title": entry.get("title", "无标题"),
                    "link": entry.get("link", ""),
                    "summary": self._clean_html(
                        entry.get("summary") or entry.get("description") or ""
                    )[:300],
                    "published": entry.get("published", ""),
                    "author": entry.get("author", ""),
                    "media_content": entry.get("media_content", []),
                }
            )
            if len(new_entries) >= max_push:
                break

        return new_entries

    @staticmethod
    async def _fetch_url(url: str) -> Optional[str]:
        """通用 HTTP GET 请求"""
        try:
            async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={"User-Agent": "Mozilla/5.0 (compatible; AstrBotRSS/1.0)"},
                )
                resp.raise_for_status()
                return resp.text
        except httpx.TimeoutException:
            logger.warning(f"请求超时: {url}")
        except httpx.HTTPStatusError as e:
            logger.warning(f"HTTP 错误 {e.response.status_code}: {url}")
        except Exception as e:
            logger.warning(f"请求失败 {url}: {e}")
        return None

    @staticmethod
    def _clean_html(text: str) -> str:
        """去除 HTML 标签，保留纯文本"""
        text = re.sub(r"<br\s*/?>", "\n", text)
        text = re.sub(r"</p>", "\n", text)
        text = re.sub(r"</(div|tr|li)>", "\n", text)
        text = re.sub(r"<[^>]+>", "", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    # ── 推送 ──

    async def _push_entries(self, feed_url: str, entries: list, feed: dict):
        """将新条目推送给所有订阅者"""
        subscribers = self.store.get_subscribers_for_feed(feed_url)
        if not subscribers:
            logger.info(f"无订阅者，跳过推送: {feed.get('title', feed_url)}")
            return

        feed_title = feed.get("title", feed_url)

        for entry in entries:
            for sub in subscribers:
                try:
                    await self.context.send_message(
                        sub["unified_origin"],
                        self._build_message_chain(entry, feed_title),
                    )
                except Exception as e:
                    logger.error(f"推送消息至 {sub.get('platform', '?')} 失败: {e}")

            await asyncio.sleep(0.5)  # 每条之间间隔，防风控

    def _build_message_chain(self, entry: dict, feed_title: str) -> list:
        """构建富文本消息组件列表"""
        components = [Plain(text=f"📰 {feed_title}")]

        # 尝试提取图片
        img_url = self._extract_image(entry)
        if img_url:
            components.append(Image(url=img_url))

        # 正文
        parts = [
            f"\n{entry['title']}",
        ]
        if entry.get("published"):
            parts.append(f"\n🕐 {entry['published']}")
        if entry.get("author"):
            parts.append(f" — 👤 {entry['author']}")
        if entry.get("summary"):
            parts.append(f"\n\n{entry['summary']}")
        if entry.get("link"):
            parts.append(f"\n\n🔗 {entry['link']}")
        components.append(Plain(text="".join(parts)))

        return components

    def _extract_image(self, entry: dict) -> Optional[str]:
        """从条目中提取第一张图片 URL"""
        # 1. media_content
        for media in entry.get("media_content", []):
            if media.get("type", "").startswith("image"):
                return media.get("url")
        # 2. media_thumbnail
        for thumb in entry.get("media_thumbnail", []):
            return thumb.get("url")
        # 3. links 中的图片
        for link in entry.get("links", []):
            if link.get("type", "").startswith("image"):
                return link.get("href")
        return None
