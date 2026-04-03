from __future__ import annotations

import asyncio
import json
import re
import random
import time
from typing import Any, Dict, List, Optional, Tuple

from pixivpy3 import AppPixivAPI, models
from src.common.logger import get_logger
from src.config.config import global_config
from src.plugin_system import BaseCommand, BasePlugin, ComponentInfo, ConfigField, register_plugin

logger = get_logger("pixiv_oauth_plugin")

# ===== Pixiv OAuth API 封类 =====


class PixivOAuthAPI:
    """Pixiv OAuth API 封装，基于官方 pixivpy 库实现。"""

    MAX_CONCURRENT_REQUESTS = 5
    MAX_NUM_PER_REQUEST = 20
    MIN_NUM_PER_REQUEST = 1
    MAX_UID_COUNT = 20
    MAX_TAG_GROUPS = 3
    SEARCH_OFFSETS = [0, 30, 60, 90, 120]

    def __init__(self, timeout: int = 30):
        self.timeout = timeout
        self.semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_REQUESTS)
        self.api = AppPixivAPI(timeout=timeout)
        
        # 猴子补丁：修复 search_illust 方法的参数映射问题
        # pixivpy3 发送 search_ai_type，但 Pixiv API 需要 show_ai
        self._patch_search_illust()

    def _pydantic_to_dict(self, obj):
        """将 Pydantic 模型对象转换为字典。"""
        if hasattr(obj, 'model_dump'):
            # Pydantic v2
            return obj.model_dump()
        elif hasattr(obj, 'dict'):
            # Pydantic v1
            return obj.dict()
        elif isinstance(obj, dict):
            # 已经是字典
            return obj
        else:
            # 尝试直接访问属性
            try:
                return dict(obj)
            except:
                return obj

    def _extract_list(self, result: Any, key: str) -> List[Dict[str, Any]]:
        """从 dict / Pydantic 模型中提取列表并统一转为 dict 列表。"""
        value = None
        if hasattr(result, key):
            value = getattr(result, key)
        elif isinstance(result, dict):
            value = result.get(key, [])

        if value is None:
            return []

        if not isinstance(value, list):
            value = [value]

        return [self._pydantic_to_dict(item) for item in value]

    def _extract_dict(self, result: Any, key: str) -> Optional[Dict[str, Any]]:
        """从 dict / Pydantic 模型中提取对象并统一转为 dict。"""
        value = None
        if hasattr(result, key):
            value = getattr(result, key)
        elif isinstance(result, dict):
            value = result.get(key)

        if value is None:
            return None
        return self._pydantic_to_dict(value)
    
    def _patch_search_illust(self):
        """修补 AppPixivAPI 方法，修复 show_ai 字段验证错误。"""
        api_instance = self.api
        
        # 保存原始方法
        self._original_search_illust = api_instance.search_illust
        self._original_load_result = api_instance._load_result
        
        # 定义修补后的 _load_result 方法
        def patched_load_result(res, model, /):
            try:
                return self._original_load_result(res, model)
            except Exception as e:
                error_str = str(e)
                # 检查是否是 show_ai 验证错误
                if "show_ai" in error_str and "Field required" in error_str:
                    logger.warning(f"捕获 show_ai 验证错误，尝试修复: {error_str[:100]}")
                    try:
                        # 解析原始 JSON
                        json_data = api_instance.parse_result(res)
                        # 添加缺失的 show_ai 字段
                        if isinstance(json_data, dict) and 'show_ai' not in json_data:
                            json_data['show_ai'] = 0  # 默认值
                            # 重新创建模型实例
                            return model.model_validate(json_data)
                    except Exception as fix_error:
                        logger.error(f"修复 show_ai 错误失败: {fix_error}")
                        # 如果修复失败，返回一个空的 SearchIllustrations 实例
                        return models.SearchIllustrations(illusts=[])
                # 重新抛出原始错误
                raise
        
        # 应用修补
        api_instance._load_result = patched_load_result
        logger.debug("已修补 AppPixivAPI._load_result 方法（修复 show_ai 字段验证）")
    
    async def ensure_token(self, access_token: str, refresh_token: str) -> bool:
        """确保 access_token 有效，必要时刷新。"""
        try:
            async with self.semaphore:
                # pixivpy 的 set_auth 需要 access_token 作为第一个位置参数
                # refresh_token 是可选的
                if access_token:
                    await asyncio.to_thread(
                        self.api.set_auth,
                        access_token,
                        refresh_token
                    )
                else:
                    # 如果没有 access_token，使用 auth 方法通过 refresh_token 获取
                    await asyncio.to_thread(
                        self.api.auth,
                        refresh_token=refresh_token
                    )
            logger.info("Pixiv access_token 设置成功")
            return True
        except Exception as e:
            logger.error(f"设置 token 异常: {e}", exc_info=True)
            return False

    async def search_illust(
        self,
        word: str,
        search_target: str = "partial_match_for_tags",
        sort: str = "date_desc",
        offset: int = 0,
        duration: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        search_ai_type: Optional[int] = None  # 注意：猴子补丁会强制设为0
    ) -> List[Dict[str, Any]]:
        """搜索插画。注意：search_ai_type参数会被猴子补丁覆盖为0。"""
        try:
            async with self.semaphore:
                # 构建参数字典
                kwargs = {
                    "word": word,
                    "search_target": search_target,
                    "sort": sort,
                    "offset": offset,
                }
                
                # 可选参数
                if duration:
                    kwargs["duration"] = duration
                if start_date:
                    kwargs["start_date"] = start_date
                if end_date:
                    kwargs["end_date"] = end_date
                if search_ai_type is not None:
                    kwargs["search_ai_type"] = search_ai_type
                
                # 调用 API（猴子补丁会强制 search_ai_type=0）
                result = await asyncio.to_thread(self.api.search_illust, **kwargs)
                
                # 处理结果：可能是 SearchIllustrations 模型实例或字典
                if hasattr(result, 'illusts'):
                    # Pydantic 模型实例
                    illusts = result.illusts
                    # 将 Pydantic 模型转换为字典
                    return [self._pydantic_to_dict(illust) for illust in illusts]
                elif isinstance(result, dict):
                    # 字典（旧版本）
                    return result.get("illusts", [])
                else:
                    logger.error(f"未知的返回类型: {type(result)}")
                    return []
                
        except Exception as e:
            logger.error(f"搜索插画异常: {e}", exc_info=True)
            return []

    async def get_user_illusts(self, user_id: int, type: str = "illust", offset: int = 0) -> List[Dict[str, Any]]:
        """获取用户插画列表。"""
        try:
            async with self.semaphore:
                result = await asyncio.to_thread(
                    self.api.user_illusts,
                    user_id=user_id,
                    type=type,
                    offset=offset
                )
            return self._extract_list(result, "illusts")
        except Exception as e:
            logger.error(f"获取用户插画异常: {e}", exc_info=True)
            return []

    async def get_user_by_name(self, username: str) -> Optional[Dict[str, Any]]:
        """根据用户名搜索用户。"""
        try:
            async with self.semaphore:
                result = await asyncio.to_thread(
                    self.api.search_user,
                    word=username,
                    sort="date_desc"
                )
            user_previews = result.get("user_previews", [])
            if user_previews:
                return user_previews[0].get("user")
            return None
        except Exception as e:
            logger.error(f"搜索用户异常: {e}", exc_info=True)
            return None

    async def get_illust_detail(self, illust_id: int) -> Optional[Dict[str, Any]]:
        """获取插画详情。"""
        try:
            async with self.semaphore:
                result = await asyncio.to_thread(
                    self.api.illust_detail,
                    illust_id=illust_id
                )
            return self._extract_dict(result, "illust")
        except Exception as e:
            logger.error(f"获取插画详情异常: {e}", exc_info=True)
            return None

    async def get_illust_ranking(self, mode: str = "day", date: Optional[str] = None) -> List[Dict[str, Any]]:
        """获取排行榜。"""
        try:
            async with self.semaphore:
                result = await asyncio.to_thread(
                    self.api.illust_ranking,
                    mode=mode,
                    date=date
                )
            return self._extract_list(result, "illusts")
        except Exception as e:
            logger.error(f"获取排行榜异常: {e}", exc_info=True)
            return []

    async def get_illust_recommended(self, content_type: str = "illust") -> List[Dict[str, Any]]:
        """获取推荐插画。"""
        try:
            async with self.semaphore:
                result = await asyncio.to_thread(
                    self.api.illust_recommended,
                    content_type=content_type
                )
            return self._extract_list(result, "illusts")
        except Exception as e:
            logger.error(f"获取推荐插画异常: {e}", exc_info=True)
            return []


class PixivCommand(BaseCommand):
    """Pixiv 图片获取命令（基于 pixivpy 官方库实现）。"""

    MAX_COOLDOWN_CACHE_SIZE = 1000
    COOLDOWN_CLEANUP_THRESHOLD = 1200
    ASPECT_RATIO_PATTERN = re.compile(r"^(gt|gte|lt|lte|eq)(\d+(?:\.\d+)?)(gt|gte|lt|lte|eq)?(\d+(?:\.\d+)?)?$")
    DATE_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")

    command_name = "pixiv"
    command_description = "通过 Pixiv OAuth 获取图片"
    command_pattern = r"^/pixiv(?:\s+(?P<args>.+))?$"
    command_help = """使用方法:
基础命令:
/pixiv - 获取1张随机图片
/pixiv help - 显示帮助信息
/pixiv random:3 - 随机搜索获取3个帖子，并分别三个帖子中的所有图片
/pixiv tag:萝莉 - 搜索含有“萝莉”tag的1个帖子，并返回其中的所有图片
/pixiv 原神 - 搜索“原神”关键词的1个帖子，并返回其中的所有图片
/pixiv tag:白丝|黑丝 - OR搜索带有“白丝”或“黑丝”tag的1个帖子，并返回其中的所有图片
/pixiv random:5 tag:风景&横图 - 组合筛选并随机返回5个帖子中的所有图片
/pixiv user:gomzi - 搜索用户名为gomzi的人发布的帖子，并随机返回一个帖子中的所有图片
/pixiv date:2016-07-15 - 搜索2016年7月15日发布的作品，并且随机返回一个帖子中的所有图片
/pixiv id:12345678 - 搜索作品ID为12345678的作品，并返回其中的所有图片"""

    command_examples = [
        "/pixiv - 获取1张随机图片",
        "/pixiv help - 显示帮助信息",
        "/pixiv random:3 - 随机获取3个帖子",
        "/pixiv tag:萝莉 - 搜索萝莉标签",
        "/pixiv 原神 - 搜索原神关键词",
        "/pixiv tag:白丝|黑丝 - OR搜索白丝或黑丝",
        "/pixiv random:5 tag:风景&横图 - 组合筛选",
        "/pixiv user:gomzi - 搜索用户gomzi的作品",
        "/pixiv date:2016-07-15 - 指定日期作品",
        "/pixiv id:12345678 - 按作品ID搜索",
    ]
    enable_command = True

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.api = PixivOAuthAPI()
        self.cooldown_cache: Dict[str, float] = {}
    
    def _convert_image_url(self, img_url: str, proxy_host: str = "i.pixiv.re") -> str:
        """将 Pixiv 原始图片 URL 转换为代理 URL。
        
        Args:
            img_url: 原始 Pixiv 图片 URL（如 https://i.pximg.net/...）
            proxy_host: 代理服务器域名（如 i.pixiv.re, pximg.cn）
        
        Returns:
            转换后的代理 URL
        """
        if not img_url:
            return img_url
        
        # 常见的 Pixiv 图片域名
        pixiv_domains = ["i.pximg.net", "img.pixiv.net", "i.pixiv.net"]
        
        for domain in pixiv_domains:
            if domain in img_url:
                # 将域名替换为代理服务器
                return img_url.replace(domain, proxy_host)
        
        # 如果不是 Pixiv 原始域名，直接返回
        return img_url

    async def execute(self) -> Tuple[bool, str, bool]:
        try:
            args_str = ""
            if self.matched_groups and "args" in self.matched_groups:
                args_str = self.matched_groups["args"] or ""
            args_str = args_str.strip()
            logger.info(f"[pixiv_oauth_plugin] 收到命令: /pixiv {args_str}".strip())

            if args_str.lower() in ["help", "帮助", "?", "？"]:
                await self.send_text(self._help_text())
                return True, "显示帮助信息", True

            user_id = self.message.message_info.user_info.user_id
            cooldown = int(self.get_config("features.cooldown_seconds", 10))
            cooldown_result = self._check_cooldown(user_id, cooldown)
            if not cooldown_result["ready"]:
                await self.send_text(f"⏰ 冷却中，还需等待 {cooldown_result['remaining']} 秒")
                return False, "冷却中", True

            # 确保 token 有效
            access_token = self.get_config("oauth.access_token", "")
            refresh_token = self.get_config("oauth.refresh_token", "")
            proxy = self.get_config("features.proxy", "")
            
            if not refresh_token:
                await self.send_text("❌ 未配置 Pixiv OAuth 信息，请在 config.toml 中配置 refresh_token")
                return False, "缺少 OAuth 配置", True
            
            # 设置代理
            if proxy:
                self.api.api.set_proxy(proxy)

            token_ok = await self.api.ensure_token(access_token, refresh_token)
            logger.info(f"[pixiv_oauth_plugin] OAuth 鉴权结果: {token_ok}")
            if not token_ok:
                await self.send_text("❌ Pixiv OAuth 鉴权失败，请检查 refresh_token 是否有效")
                return False, "OAuth 鉴权失败", True

            params = self._parse_args(args_str)
            logger.info(f"[pixiv_oauth_plugin] 解析参数: {json.dumps(params, ensure_ascii=False)}")
            ok = await self._fetch_pixiv(params)
            if ok:
                self._update_cooldown(user_id)
                return True, "已发送图片", True
            return False, "获取失败", True

        except Exception as e:
            logger.error(f"命令执行失败: {e}", exc_info=True)
            await self.send_text(f"❌ 执行失败: {e}")
            return False, f"执行失败: {e}", True

    def _help_text(self) -> str:
        return """🎨 Pixiv 图片插件使用帮助

📌 基础命令:
  /pixiv              获取1张随机图片
  /pixiv help         显示此帮助信息
  /pixiv random:3     随机获取3个帖子的所有图片

🏷️ 标签搜索:
  /pixiv tag:萝莉      搜索萝莉标签
  /pixiv tag:白丝|黑丝   OR搜索（白丝或黑丝）
  /pixiv tag:萝莉&白丝  AND搜索（萝莉且白丝）

🔍 关键词搜索:
  /pixiv 原神         直接搜索原神关键词
  /pixiv keyword:初音未来  显式指定关键词搜索

👤 用户搜索:
  /pixiv user:gomzi   搜索用户名为gomzi的作品
  /pixiv uid:12345    按用户ID搜索

🆔 作品ID搜索:
  /pixiv id:12345678  获取指定ID的作品

📅 日期搜索:
  /pixiv date:2016-07-15  获取指定日期的作品

📐 长宽比筛选:
  /pixiv 横图         横图 (长宽比>1)
  /pixiv 竖图         竖图 (长宽比<1)
  /pixiv 方图         方图 (长宽比=1)
  /pixiv gt1.5        自定义长宽比大于1.5

🤖 其他选项:
  /pixiv noai         排除AI作品
  /pixiv r18          R18内容 (需配置允许)

✨ 组合使用:
  /pixiv random:5 tag:萝莉|白丝 横图 noai
  /pixiv 原神 tag:胡桃 5 r18
  /pixiv user:gomzi 3 竖图"""

    def _parse_args(self, args_str: str) -> Dict[str, Any]:
        params: Dict[str, Any] = {
            "num": int(self.get_config("features.default_num", 1)),
            "r18": 0,
            "exclude_ai": bool(self.get_config("features.default_exclude_ai", True)),
            "keyword": None,
            "tag_and": [],
            "tag_or": [],
            "user_id": None,
            "username": None,
            "illust_id": None,
            "date": None,
            "aspect_ratio": None,
            "random": False,
        }
        if not args_str:
            return params

        reserved_keywords = {
            "r18",
            "noai",
            "no_ai",
            "排除ai",
            "horizontal",
            "横图",
            "vertical",
            "竖图",
            "square",
            "方图",
        }

        for arg in args_str.split():
            lower = arg.lower()
            if lower.isdigit():
                params["num"] = min(max(PixivOAuthAPI.MIN_NUM_PER_REQUEST, int(lower)), PixivOAuthAPI.MAX_NUM_PER_REQUEST)
            elif lower == "r18":
                if self.get_config("features.allow_r18", False):
                    params["r18"] = 1
            elif lower in ["noai", "no_ai", "排除ai"]:
                params["exclude_ai"] = True
            elif lower in ["horizontal", "横图"]:
                params["aspect_ratio"] = "gt1"
            elif lower in ["vertical", "竖图"]:
                params["aspect_ratio"] = "lt1"
            elif lower in ["square", "方图"]:
                params["aspect_ratio"] = "eq1"
            elif arg.startswith("random:"):
                params["random"] = True
                try:
                    num = int(arg.split(":", 1)[1])
                    params["num"] = min(max(PixivOAuthAPI.MIN_NUM_PER_REQUEST, num), PixivOAuthAPI.MAX_NUM_PER_REQUEST)
                except ValueError:
                    pass
            elif arg.startswith("tag:"):
                tag_str = arg.split(":", 1)[1]
                if "|" in tag_str:
                    params["tag_or"].extend([t.strip() for t in tag_str.split("|") if t.strip()])
                else:
                    params["tag_and"].append(tag_str.strip())
            elif arg.startswith("user:"):
                params["username"] = arg.split(":", 1)[1].strip()
            elif arg.startswith("uid:"):
                try:
                    params["user_id"] = int(arg.split(":", 1)[1])
                except ValueError:
                    logger.warning(f"无效UID: {arg}")
            elif arg.startswith("id:"):
                try:
                    params["illust_id"] = int(arg.split(":", 1)[1])
                except ValueError:
                    logger.warning(f"无效作品ID: {arg}")
            elif arg.startswith("date:"):
                date_str = arg.split(":", 1)[1].strip()
                if self.DATE_PATTERN.match(date_str):
                    params["date"] = date_str
                else:
                    logger.warning(f"无效日期格式: {arg}, 请使用 YYYY-MM-DD 格式")
            elif arg.startswith("keyword:") or arg.startswith("kw:"):
                params["keyword"] = arg.split(":", 1)[1].strip()
            elif self._validate_aspect_ratio(lower):
                params["aspect_ratio"] = lower
            elif lower not in reserved_keywords:
                if params["keyword"]:
                    params["keyword"] += " " + arg
                else:
                    params["keyword"] = arg

        return params

    def _validate_aspect_ratio(self, ratio_str: str) -> bool:
        if not ratio_str:
            return False
        m = self.ASPECT_RATIO_PATTERN.match(ratio_str)
        if not m:
            return False
        groups = m.groups()
        try:
            ratio1 = float(groups[1])
            if ratio1 < 0.1 or ratio1 > 10:
                return False
            if groups[2] and groups[3]:
                ratio2 = float(groups[3])
                if ratio2 < 0.1 or ratio2 > 10:
                    return False
            return True
        except (ValueError, IndexError):
            return False

    async def _fetch_pixiv(self, params: Dict[str, Any]) -> bool:
        """从 Pixiv API 获取图片。"""
        wanted = min(max(PixivOAuthAPI.MIN_NUM_PER_REQUEST, params["num"]), PixivOAuthAPI.MAX_NUM_PER_REQUEST)
        logger.info(f"[pixiv_oauth_plugin] 目标返回数量: {wanted}")

        # 收集候选插画
        illusts: List[Dict[str, Any]] = []

        # 按作品ID获取（最高优先级）
        if params.get("illust_id"):
            illust = await self.api.get_illust_detail(params["illust_id"])
            if illust:
                illusts.append(illust)

        # 按用户获取
        elif params.get("user_id") or params.get("username"):
            if params.get("username"):
                user = await self.api.get_user_by_name(params["username"])
                if not user:
                    await self.send_text(f"😢 未找到用户 {params['username']}")
                    return False
                params["user_id"] = user["id"]
            
            if params["user_id"]:
                # 获取多页用户作品
                offset = 0
                while len(illusts) < wanted * 10 and offset < 300:
                    items = await self.api.get_user_illusts(params["user_id"], offset=offset)
                    if not items:
                        break
                    illusts.extend(items)
                    offset += 30

        # 按日期获取
        elif params.get("date"):
            items = await self.api.get_illust_ranking("day", date=params["date"])
            illusts.extend(items)

        # 按关键词/标签搜索
        else:
            search_queries = []
            
            # 组合搜索词
            if params.get("tag_and"):
                and_tags = " ".join([f"#{tag}" for tag in params["tag_and"]])
                search_queries.append(and_tags)
            
            if params.get("tag_or"):
                or_tags = "|".join(params["tag_or"])
                search_queries.append(or_tags)
            
            if params.get("keyword"):
                search_queries.append(params["keyword"])
            
            if not search_queries:
                # 无搜索条件时使用排行榜（推荐功能可能有兼容性问题）
                items = await self.api.get_illust_ranking("day")
                illusts.extend(items)
            else:
                search_word = " ".join(search_queries)
                # 注意：不再设置 search_ai_type 参数，猴子补丁会强制设为0
                # AI过滤在本地通过 illust_ai_type 字段处理
                
                # 多offset获取更多结果
                for offset in [0, 30, 60, 90]:
                    items = await self.api.search_illust(
                        search_word,
                        search_target="partial_match_for_tags",
                        offset=offset
                        # 不传递 search_ai_type，由猴子补丁处理
                    )
                    if not items:
                        break
                    illusts.extend(items)

        logger.info(f"[pixiv_oauth_plugin] 候选作品数量(过滤前): {len(illusts)}")
        if not illusts:
            await self.send_text("😢 没有找到符合条件的图片")
            return False

        # 过滤
        filtered = self._filter_illusts(illusts, params)
        logger.info(f"[pixiv_oauth_plugin] 候选作品数量(过滤后): {len(filtered)}")
        
        # 零结果自动降级策略
        if not filtered and self.get_config("features.enable_auto_degradation", True):
            degradation_tried = []
            
            # 尝试关闭AI过滤
            if params.get("exclude_ai"):
                original_exclude_ai = params["exclude_ai"]
                params["exclude_ai"] = False
                degradation_tried.append("关闭AI过滤")
                logger.info(f"[pixiv_oauth_plugin] 零结果降级：尝试关闭AI过滤")
                filtered = self._filter_illusts(illusts, params)
                logger.info(f"[pixiv_oauth_plugin] 降级后候选数量: {len(filtered)}")
                params["exclude_ai"] = original_exclude_ai  # 恢复原值
            
            if filtered:
                degradation_msg = "，".join(degradation_tried)
                await self.send_text(f"⚠️ 已自动放宽过滤条件（{degradation_msg}）以获取结果")
        
        if not filtered:
            await self.send_text("😢 过滤后没有符合条件的图片")
            return False

        # 随机选择
        if params.get("random") or len(filtered) > wanted:
            selected = random.sample(filtered, min(wanted, len(filtered)))
        else:
            selected = filtered[:wanted]

        use_forward = bool(self.get_config("features.use_forward_message", True))

        if use_forward:
            # 使用合并转发格式发送
            forward_messages = []
            bot_qq = str(global_config.bot.qq_account)
            bot_name = str(global_config.bot.nickname)

            for i, illust in enumerate(selected, 1):
                # 构建信息文本
                title = illust.get("title", "无标题")
                user_name = illust.get("user", {}).get("name", "未知作者")
                user_id = illust.get("user", {}).get("id", "0")
                illust_id = illust.get("id", "0")
                tags = ", ".join([tag.get("name", "") for tag in illust.get("tags", [])])
                width = illust.get("width", 0)
                height = illust.get("height", 0)

                info_text = f"【{i}/{len(selected)}】{title}\n"
                info_text += f"👤 {user_name} (ID: {user_id})\n"
                info_text += f"🆔 PID: {illust_id}\n"
                info_text += f"📏 {width}x{height}\n"

                # AI标签
                ai_type = illust.get("ai_type", 0)
                if ai_type == 2:
                    info_text += "🤖 AI作品\n"

                # R18标签
                x_restrict = illust.get("x_restrict", 0)
                if x_restrict == 1:
                    info_text += "🔞 R18\n"
                elif x_restrict == 2:
                    info_text += "🔞 R18G\n"

                # 标签
                if tags:
                    info_text += f"🏷️ {tags[:50]}"

                # 获取作品全部图片 URL（多图不再只取首图）
                image_urls = self._extract_illust_image_urls(illust)
                if image_urls:
                    image_proxy = str(self.get_config("features.image_proxy", "i.pixiv.re"))
                    total_pages = len(image_urls)

                    for page_idx, img_url in enumerate(image_urls, 1):
                        proxy_img_url = self._convert_image_url(img_url, image_proxy)
                        page_text = info_text
                        if total_pages > 1:
                            page_text += f"\n📸 第 {page_idx}/{total_pages} 张"

                        # 每张图一个转发节点，确保完整返回
                        message_content = [("text", page_text), ("imageurl", proxy_img_url)]
                        forward_messages.append((bot_qq, bot_name, message_content))

            # 发送合并转发消息
            if forward_messages:
                await self.send_forward(forward_messages, storage_message=True)
                return True
        else:
            # 传统方式发送（一条条发送）
            for i, illust in enumerate(selected, 1):
                # 构建信息文本
                title = illust.get("title", "无标题")
                user_name = illust.get("user", {}).get("name", "未知作者")
                user_id = illust.get("user", {}).get("id", "0")
                illust_id = illust.get("id", "0")
                tags = ", ".join([tag.get("name", "") for tag in illust.get("tags", [])])
                width = illust.get("width", 0)
                height = illust.get("height", 0)

                info_text = f"【{i}/{len(selected)}】{title}\n"
                info_text += f"👤 {user_name} (ID: {user_id})\n"
                info_text += f"🆔 PID: {illust_id}\n"
                info_text += f"📏 {width}x{height}\n"

                # AI标签
                ai_type = illust.get("ai_type", 0)
                if ai_type == 2:
                    info_text += "🤖 AI作品\n"

                # R18标签
                x_restrict = illust.get("x_restrict", 0)
                if x_restrict == 1:
                    info_text += "🔞 R18\n"
                elif x_restrict == 2:
                    info_text += "🔞 R18G\n"

                # 标签
                if tags:
                    info_text += f"🏷️ {tags[:50]}\n"

                # 获取作品全部图片 URL（多图不再只取首图）
                image_urls = self._extract_illust_image_urls(illust)
                if image_urls:
                    image_proxy = str(self.get_config("features.image_proxy", "i.pixiv.re"))

                    # 先发送文本（标注页数）
                    if len(image_urls) > 1:
                        await self.send_text(info_text + f"🖼️ 共 {len(image_urls)} 张")
                    else:
                        await self.send_text(info_text)

                    # 顺序发送该作品的全部图片
                    for img_url in image_urls:
                        proxy_img_url = self._convert_image_url(img_url, image_proxy)
                        try:
                            await self.send_custom("imageurl", proxy_img_url)
                        except Exception as e:
                            logger.error(f"发送图片失败: {str(e)}")
                            await self.send_text("⚠️ 图片发送失败，请使用上方链接查看")

                    # 间隔避免刷屏
                    if i < len(selected):
                        await asyncio.sleep(1)

        return True

    def _filter_illusts(self, illusts: List[Dict[str, Any]], params: Dict[str, Any]) -> List[Dict[str, Any]]:
        """过滤插画列表。"""
        filtered = []
        allow_r18 = self.get_config("features.allow_r18", False)
        r18_mode = params.get("r18", 0)

        for illust in illusts:
            # R18过滤
            x_restrict = illust.get("x_restrict", 0)
            if x_restrict == 1 and (not allow_r18 or r18_mode == 0):
                continue
            if x_restrict == 2 and not self.get_config("features.allow_r18g", False):
                continue

            # AI作品过滤
            if params.get("exclude_ai") and illust.get("ai_type", 0) == 2:
                continue

            # 长宽比过滤
            if params.get("aspect_ratio"):
                width = illust.get("width", 1)
                height = illust.get("height", 1)
                aspect_ratio = width / height
                if not self._match_aspect_ratio(aspect_ratio, params["aspect_ratio"]):
                    continue

            filtered.append(illust)

        return filtered

    def _match_aspect_ratio(self, aspect_ratio: float, pattern: str) -> bool:
        """匹配长宽比规则。"""
        m = self.ASPECT_RATIO_PATTERN.match(pattern)
        if not m:
            return True
        
        op1, val1, op2, val2 = m.groups()
        val1 = float(val1)
        
        # 第一个条件
        match1 = False
        if op1 == "gt":
            match1 = aspect_ratio > val1
        elif op1 == "gte":
            match1 = aspect_ratio >= val1
        elif op1 == "lt":
            match1 = aspect_ratio < val1
        elif op1 == "lte":
            match1 = aspect_ratio <= val1
        elif op1 == "eq":
            match1 = abs(aspect_ratio - val1) < 0.01
        
        if not op2 or not val2:
            return match1
        
        # 第二个条件
        val2 = float(val2)
        match2 = False
        if op2 == "gt":
            match2 = aspect_ratio > val2
        elif op2 == "gte":
            match2 = aspect_ratio >= val2
        elif op2 == "lt":
            match2 = aspect_ratio < val2
        elif op2 == "lte":
            match2 = aspect_ratio <= val2
        elif op2 == "eq":
            match2 = abs(aspect_ratio - val2) < 0.01
        
        return match1 and match2

    def _extract_illust_image_urls(self, illust: Dict[str, Any]) -> List[str]:
        """提取作品内全部图片 URL（按页顺序，自动去重，兼容单图/多图结构）。"""
        urls: List[str] = []

        meta_pages = illust.get("meta_pages", []) or []
        for page in meta_pages:
            image_urls = page.get("image_urls", {}) or {}
            img_url = image_urls.get("original") or image_urls.get("large") or ""
            if img_url:
                urls.append(img_url)

        if not urls:
            img_url = illust.get("meta_single_page", {}).get("original_image_url", "")
            if not img_url:
                img_url = illust.get("image_urls", {}).get("large", "")
            if img_url:
                urls.append(img_url)

        unique_urls: List[str] = []
        seen = set()
        for url in urls:
            if url not in seen:
                seen.add(url)
                unique_urls.append(url)
        return unique_urls

    def _build_illust_entries(self, illust: Dict[str, Any]) -> List[str]:
        """构建插画消息条目，包含所有图片。"""
        entries = []
        title = illust.get("title", "无标题")
        user_name = illust.get("user", {}).get("name", "未知作者")
        user_id = illust.get("user", {}).get("id", "0")
        illust_id = illust.get("id", "0")
        tags = ", ".join([tag.get("name", "") for tag in illust.get("tags", [])])
        
        info_text = f"""🎨 作品: {title}
👤 作者: {user_name} (ID: {user_id})
🆔 作品ID: {illust_id}
🏷️ 标签: {tags}
🔗 链接: https://www.pixiv.net/artworks/{illust_id}"""

        # 获取所有图片URL
        meta_pages = illust.get("meta_pages", [])
        if meta_pages:
            # 多图作品
            for idx, page in enumerate(meta_pages, 1):
                img_url = page.get("image_urls", {}).get("original", "")
                if img_url:
                    entries.append(f"{info_text}\n📸 第 {idx} 张\n[CQ:image,file={img_url}]")
        else:
            # 单图作品
            img_url = illust.get("meta_single_page", {}).get("original_image_url", "")
            if not img_url:
                img_url = illust.get("image_urls", {}).get("large", "")
            if img_url:
                entries.append(f"{info_text}\n[CQ:image,file={img_url}]")

        return entries

    def _check_cooldown(self, user_id: str, cooldown_seconds: int) -> Dict[str, Any]:
        """检查用户冷却状态。"""
        now = time.time()
        last_time = self.cooldown_cache.get(user_id, 0.0)
        remaining = cooldown_seconds - (now - last_time)
        
        if remaining <= 0:
            return {"ready": True, "remaining": 0}
        return {"ready": False, "remaining": int(remaining)}

    def _update_cooldown(self, user_id: str):
        """更新用户冷却时间。"""
        self.cooldown_cache[user_id] = time.time()
        # 清理过期缓存
        if len(self.cooldown_cache) > self.MAX_COOLDOWN_CACHE_SIZE:
            now = time.time()
            expired = [k for k, v in self.cooldown_cache.items() if now - v > self.COOLDOWN_CLEANUP_THRESHOLD]
            for k in expired:
                del self.cooldown_cache[k]


# ===== Pixiv 插件主类 =====

@register_plugin
class PixivOAuthPlugin(BasePlugin):
    """Pixiv OAuth 插件，基于 pixivpy 官方库实现。"""

    # 插件基本信息
    plugin_name = "pixiv_oauth_plugin"
    enable_plugin = True
    dependencies = []
    python_dependencies = ["pixivpy3"]
    config_file_name = "config.toml"

    # 配置Schema定义
    config_schema = {
        "plugin": {
            "enabled": ConfigField(type=bool, default=True, description="是否启用插件"),
            "config_version": ConfigField(type=str, default="2.0.0", description="配置文件版本"),
        },
        "oauth": {
            "access_token": ConfigField(type=str, default="", description="Pixiv OAuth Access Token（可选）"),
            "refresh_token": ConfigField(type=str, default="", description="Pixiv OAuth Refresh Token（必需）"),
        },
        "features": {
            "default_num": ConfigField(type=int, default=1, description="默认返回图片数量（1-20）"),
            "cooldown_seconds": ConfigField(type=int, default=10, description="命令冷却时间（秒）"),
            "allow_r18": ConfigField(type=bool, default=False, description="是否允许R18内容"),
            "allow_r18g": ConfigField(type=bool, default=False, description="是否允许R18G内容"),
            "default_exclude_ai": ConfigField(type=bool, default=True, description="是否默认排除AI作品"),
            "use_forward_message": ConfigField(type=bool, default=True, description="是否使用合并转发消息发送多张图片"),
            "enable_auto_degradation": ConfigField(type=bool, default=True, description="零结果时是否自动降级过滤条件"),
            "proxy": ConfigField(type=str, default="", description="代理服务器地址（可选，如 http://127.0.0.1:7890）"),
            "image_proxy": ConfigField(type=str, default="i.pixiv.re", description="图片代理服务器，用于转换Pixiv原始图片URL（如i.pixiv.re, pximg.cn）"),
        },
    }

    def get_plugin_components(self) -> List[Tuple[ComponentInfo, type]]:
        """返回插件包含的组件列表"""
        return [
            (PixivCommand.get_command_info(), PixivCommand),
        ]

    async def on_load(self):
        """插件加载时执行。"""
        logger.info("Pixiv OAuth 插件加载成功，基于 pixivpy 官方库实现")

    async def on_unload(self):
        """插件卸载时执行。"""
        logger.info("Pixiv OAuth 插件已卸载")
