from __future__ import annotations

import base64
import random
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from loguru import logger

from src.plugin_system import BasePlugin, BaseCommand, ComponentInfo, register_plugin
from src.plugin_system.apis import plugin_config_api


class PixivCommand(BaseCommand):
    """最小首版：支持 /pixiv 返回一张图。"""

    command_name = "pixiv"
    command_description = "获取一张 Pixiv 图片"
    command_pattern = r"^/pixiv(?:\s+(.+))?$"

    async def execute(self) -> Tuple[bool, Optional[str], bool]:
        config: Dict[str, Any] = self.plugin.get_config("pixiv_oauth") or {}

        image_urls: List[str] = [
            u.strip() for u in (config.get("fallback_image_urls") or []) if isinstance(u, str) and u.strip()
        ]

        if not image_urls:
            await self.send_text("pixiv_oauth_plugin 未配置 fallback_image_urls，无法返回图片。", set_reply=True, reply_message=self.message)
            return False, "missing fallback_image_urls", True

        random.shuffle(image_urls)

        timeout = aiohttp.ClientTimeout(total=float(config.get("download_timeout_seconds", 15)))

        async with aiohttp.ClientSession(trust_env=True, timeout=timeout) as session:
            for url in image_urls:
                image_bytes = await self._download_image(session, url)
                if not image_bytes:
                    continue

                b64 = base64.b64encode(image_bytes).decode("utf-8")
                ok = await self.send_image(b64, set_reply=True, reply_message=self.message)
                if ok:
                    logger.info(f"[pixiv_oauth_plugin] /pixiv sent image from: {url}")
                    return True, None, True

        await self.send_text("图片下载或发送失败，稍后再试喵。", set_reply=True, reply_message=self.message)
        return False, "all candidate images failed", True

    async def _download_image(self, session: aiohttp.ClientSession, url: str) -> Optional[bytes]:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://www.pixiv.net/",
        }
        try:
            async with session.get(url, headers=headers) as resp:
                if resp.status != 200:
                    logger.warning(f"[pixiv_oauth_plugin] download failed status={resp.status}, url={url}")
                    return None
                data = await resp.read()
                if not data:
                    return None
                return data
        except Exception as e:
            logger.warning(f"[pixiv_oauth_plugin] download exception url={url}, err={e}")
            return None


@register_plugin
class PixivOauthPlugin(BasePlugin):
    plugin_name = "pixiv_oauth_plugin"
    enable_plugin = True
    config_file_name = "config.toml"

    config_schema = {
        "pixiv_oauth": {
            "fallback_image_urls": plugin_config_api.ConfigField(
                type=list,
                default=[],
                description="/pixiv 兜底图片 URL 列表（首版最小实现）",
            ),
            "download_timeout_seconds": plugin_config_api.ConfigField(
                type=int,
                default=15,
                description="图片下载超时秒数",
            ),
        }
    }

    def get_plugin_components(self) -> List[ComponentInfo]:
        return [ComponentInfo(component_class=PixivCommand, enabled=True)]
