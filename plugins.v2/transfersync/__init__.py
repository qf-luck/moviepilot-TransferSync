import os
import re
import shutil
import threading
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from concurrent.futures import ThreadPoolExecutor
import time

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.core.event import Event, EventType, eventmanager
from app.core.cache import cached, TTLCache
from app.plugins import _PluginBase
from app.log import logger
from app.schemas.types import EventType, NotificationType
from app.schemas import NotificationConf
from app.helper.notification import NotificationHelper
from app.helper.mediaserver import MediaServerHelper


class SyncStrategy(Enum):
    """同步策略"""
    COPY = "copy"           # 复制
    MOVE = "move"           # 移动
    HARDLINK = "hardlink"   # 硬链接
    SOFTLINK = "softlink"   # 软链接


class SyncMode(Enum):
    """同步模式"""
    IMMEDIATE = "immediate"     # 立即同步
    BATCH = "batch"            # 批量同步
    QUEUE = "queue"            # 队列同步


class FileFilterType(Enum):
    """文件过滤类型"""
    EXTENSION = "extension"     # 扩展名过滤
    SIZE = "size"              # 文件大小过滤
    REGEX = "regex"            # 正则表达式过滤
    PATH = "path"              # 路径过滤


class SyncStatus(Enum):
    """同步状态"""
    IDLE = "idle"              # 空闲
    RUNNING = "running"        # 运行中
    PAUSED = "paused"          # 暂停
    ERROR = "error"            # 错误
    COMPLETED = "completed"    # 完成


class TriggerEvent(Enum):
    """可选择的触发事件"""
    TRANSFER_COMPLETE = "transfer.complete"        # 整理完成（默认）
    DOWNLOAD_ADDED = "download.added"             # 下载已添加
    SUBSCRIBE_COMPLETE = "subscribe.complete"     # 订阅已完成
    METADATA_SCRAPE = "metadata.scrape"          # 刮削元数据
    WEBHOOK_MESSAGE = "webhook.message"          # Webhook消息
    USER_MESSAGE = "user.message"                # 用户消息
    PLUGIN_TRIGGERED = "plugin.triggered"        # 插件触发事件
    MEDIA_ADDED = "media.added"                  # 媒体已添加
    FILE_MOVED = "file.moved"                    # 文件已移动
    DIRECTORY_SCAN_COMPLETE = "directory.scan.complete"  # 目录扫描完成
    SCRAPE_COMPLETE = "scrape.complete"          # 刮削完成
    
    @classmethod
    def get_display_names(cls) -> Dict[str, str]:
        """获取事件显示名称"""
        return {
            cls.TRANSFER_COMPLETE.value: "整理完成",
            cls.DOWNLOAD_ADDED.value: "下载添加", 
            cls.SUBSCRIBE_COMPLETE.value: "订阅完成",
            cls.METADATA_SCRAPE.value: "元数据刮削",
            cls.WEBHOOK_MESSAGE.value: "Webhook消息",
            cls.USER_MESSAGE.value: "用户消息",
            cls.PLUGIN_TRIGGERED.value: "插件触发",
            cls.MEDIA_ADDED.value: "媒体添加",
            cls.FILE_MOVED.value: "文件移动",
            cls.DIRECTORY_SCAN_COMPLETE.value: "目录扫描完成",
            cls.SCRAPE_COMPLETE.value: "刮削完成"
        }


class EventCondition(Enum):
    """事件过滤条件类型"""
    MEDIA_TYPE = "media_type"          # 媒体类型（电影/电视剧）
    SOURCE_PATH = "source_path"        # 源路径匹配
    TARGET_PATH = "target_path"        # 目标路径匹配
    FILE_SIZE = "file_size"           # 文件大小
    FILE_EXTENSION = "file_extension"  # 文件扩展名


# 事件处理装饰器和工具类
def event_handler(event_type: TriggerEvent):
    """统一事件处理装饰器"""
    def decorator(func):
        def wrapper(self, event: Event):
            start_time = time.time()
            try:
                # 预处理检查
                if not self._should_handle_event(event, event_type):
                    return False
                    
                logger.info(f"收到{self._get_event_display_name(event_type.value)}事件: {event}")
                
                # 执行具体的事件处理
                result = func(self, event)
                
                # 记录成功统计
                processing_time = time.time() - start_time
                self._update_event_stats(event_type, True, processing_time)
                logger.debug(f"{event_type.value}事件处理成功，耗时: {processing_time:.2f}s")
                
                return result
                
            except PermissionError as e:
                logger.error(f"处理{event_type.value}事件时权限错误: {str(e)}")
                self._update_event_stats(event_type, False, time.time() - start_time, error_type="PermissionError")
                return False
            except OSError as e:
                logger.error(f"处理{event_type.value}事件时文件系统错误: {str(e)}")
                self._update_event_stats(event_type, False, time.time() - start_time, error_type="OSError")
                return False
            except Exception as e:
                logger.error(f"处理{event_type.value}事件时未知错误: {str(e)}")
                self._update_event_stats(event_type, False, time.time() - start_time, error_type="UnknownError")
                return False
        return wrapper
    return decorator


class SyncException(Exception):
    """同步操作异常基类"""
    pass


class SyncPermissionError(SyncException):
    """同步权限错误"""
    pass


class SyncSpaceError(SyncException):
    """磁盘空间不足错误"""
    pass


class SyncNetworkError(SyncException):
    """网络连接错误"""
    pass


class AtomicFileOperation:
    """原子性文件操作管理器"""
    def __init__(self):
        self.backup_files = []
        self.created_files = []
        self.created_dirs = []
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 发生异常，执行回滚
            self._rollback()
        return False
    
    def _rollback(self):
        """回滚操作"""
        logger.info("执行原子操作回滚")
        
        # 删除创建的文件
        for file_path in reversed(self.created_files):
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"回滚删除文件: {file_path}")
            except Exception as e:
                logger.warning(f"回滚删除文件失败 {file_path}: {str(e)}")
        
        # 删除创建的目录
        for dir_path in reversed(self.created_dirs):
            try:
                if dir_path.exists() and not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    logger.debug(f"回滚删除目录: {dir_path}")
            except Exception as e:
                logger.warning(f"回滚删除目录失败 {dir_path}: {str(e)}")
    
    def track_created_file(self, file_path: Path):
        """跟踪创建的文件"""
        self.created_files.append(file_path)
    
    def track_created_dir(self, dir_path: Path):
        """跟踪创建的目录"""
        self.created_dirs.append(dir_path)


class ConfigValidator:
    """高级配置验证器"""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        
    def validate_paths(self, paths: List[str]) -> bool:
        """验证路径配置"""
        valid = True
        
        if not paths:
            self.errors.append("未配置同步目标路径")
            return False
            
        for path_str in paths:
            try:
                path = Path(path_str)
                
                # 检查路径是否存在
                if not path.exists():
                    try:
                        # 尝试创建路径
                        path.mkdir(parents=True, exist_ok=True)
                        self.warnings.append(f"自动创建目标路径: {path}")
                    except Exception as e:
                        self.errors.append(f"无法创建目标路径 {path}: {str(e)}")
                        valid = False
                        continue
                
                # 检查是否为目录
                if not path.is_dir():
                    self.errors.append(f"路径不是目录: {path}")
                    valid = False
                    continue
                    
                # 检查写权限
                if not os.access(path, os.W_OK):
                    self.errors.append(f"路径无写权限: {path}")
                    valid = False
                    continue
                    
                # 检查磁盘空间
                try:
                    free_space = shutil.disk_usage(path).free
                    if free_space < 1024 * 1024 * 100:  # 小于100MB
                        self.warnings.append(f"路径可用空间较少 ({free_space / 1024 / 1024:.1f}MB): {path}")
                except Exception:
                    self.warnings.append(f"无法获取磁盘空间信息: {path}")
                    
            except Exception as e:
                self.errors.append(f"路径验证失败 {path_str}: {str(e)}")
                valid = False
        
        return valid
    
    def validate_cron_expressions(self, expressions: Dict[str, str]) -> bool:
        """验证Cron表达式"""
        valid = True
        
        for name, expression in expressions.items():
            if not expression:
                continue
                
            try:
                CronTrigger.from_crontab(expression)
            except Exception as e:
                self.errors.append(f"{name}Cron表达式无效 '{expression}': {str(e)}")
                valid = False
                
        return valid
    
    def validate_numeric_ranges(self, configs: Dict[str, tuple]) -> bool:
        """验证数值范围配置"""
        valid = True
        
        for name, (value, min_val, max_val) in configs.items():
            if value < min_val:
                self.errors.append(f"{name}不能小于{min_val}，当前值: {value}")
                valid = False
            elif max_val is not None and value > max_val:
                self.errors.append(f"{name}不能大于{max_val}，当前值: {value}")
                valid = False
                
        return valid
    
    def validate_regex_patterns(self, patterns: List[str]) -> bool:
        """验证正则表达式模式"""
        valid = True
        
        for pattern in patterns:
            if not pattern:
                continue
                
            try:
                re.compile(pattern)
            except re.error as e:
                self.errors.append(f"正则表达式无效 '{pattern}': {str(e)}")
                valid = False
                
        return valid
    
    def validate_file_extensions(self, extensions: List[str]) -> bool:
        """验证文件扩展名配置"""
        valid = True
        
        for ext in extensions:
            if not ext:
                continue
                
            # 确保扩展名以.开头
            if not ext.startswith('.'):
                self.warnings.append(f"文件扩展名建议以'.'开头: {ext}")
            
            # 检查是否包含无效字符
            invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
            if any(char in ext for char in invalid_chars):
                self.errors.append(f"文件扩展名包含无效字符: {ext}")
                valid = False
                
        return valid
    
    def validate_trigger_events(self, events: List[str]) -> bool:
        """验证触发事件配置"""
        valid = True
        
        if not events:
            self.warnings.append("未选择任何触发事件，插件将不会自动触发")
            return True
            
        valid_events = [e.value for e in TriggerEvent]
        for event in events:
            if event not in valid_events:
                self.errors.append(f"无效的触发事件类型: {event}")
                valid = False
                
        return valid
    
    def get_summary(self) -> Dict[str, Any]:
        """获取验证摘要"""
        return {
            'valid': len(self.errors) == 0,
            'error_count': len(self.errors),
            'warning_count': len(self.warnings),
            'errors': self.errors,
            'warnings': self.warnings
        }
    
    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0
    
    def has_warnings(self) -> bool:
        """是否有警告"""
        return len(self.warnings) > 0
    
    def clear(self):
        """清除验证结果"""
        self.errors.clear()
        self.warnings.clear()


class TransferSync(_PluginBase):
    # 插件名称
    plugin_name = "整理后同步"
    # 插件描述
    plugin_desc = "监听可选择的多种事件类型，根据过滤条件自动同步文件到指定位置，支持多种同步策略、事件统计监控、增量和全量同步。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/sync.png"
    # 插件版本
    plugin_version = "1.3"
    # 插件作者
    plugin_author = "MoviePilot"
    # 作者主页
    author_url = "https://github.com/jxxghp/MoviePilot-Plugins"
    # 插件配置项ID前缀
    plugin_config_prefix = "transfersync_"
    # 加载顺序
    plugin_order = 20
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    _copy_paths = []
    _enable_incremental = False
    _incremental_cron = "0 */6 * * *"
    _enable_full_sync = False
    _full_sync_cron = "0 2 * * 0"
    _enable_notifications = False
    _notification_channels = []
    _sync_strategy = SyncStrategy.COPY
    _sync_mode = SyncMode.IMMEDIATE
    _max_depth = -1  # -1表示无限制
    _file_filters = []
    _exclude_patterns = []
    _max_file_size = 0  # 0表示无限制，单位MB
    _min_file_size = 0  # 单位MB
    _enable_progress = True
    _max_workers = 4
    _batch_size = 100
    # 事件触发相关配置
    _trigger_events = [TriggerEvent.TRANSFER_COMPLETE]  # 默认监听整理完成事件
    _event_conditions = {}  # 事件过滤条件
    _event_statistics = {}  # 事件统计信息
    _scheduler = None
    _last_sync_time = None
    _sync_records = {}
    _sync_progress = {}
    _current_status = SyncStatus.IDLE
    _sync_queue = []
    _lock = threading.Lock()
    _progress_lock = threading.Lock()
    _cache = None
    _notification_helper = None
    _mediaserver_helper = None
    _executor = None

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        if config:
            self._enabled = config.get("enabled", False)
            
            # 基础配置
            self._copy_paths = self._parse_paths(config.get("copy_paths", ""))
            self._enable_incremental = config.get("enable_incremental", False)
            self._incremental_cron = config.get("incremental_cron", "0 */6 * * *")
            self._enable_full_sync = config.get("enable_full_sync", False)
            self._full_sync_cron = config.get("full_sync_cron", "0 2 * * 0")
            self._enable_notifications = config.get("enable_notifications", False)
            self._notification_channels = self._parse_list(config.get("notification_channels", ""))
            
            # 高级配置
            self._sync_strategy = SyncStrategy(config.get("sync_strategy", "copy"))
            self._sync_mode = SyncMode(config.get("sync_mode", "immediate"))
            self._max_depth = config.get("max_depth", -1)
            self._file_filters = self._parse_list(config.get("file_filters", ""))
            self._exclude_patterns = self._parse_list(config.get("exclude_patterns", ""))
            self._max_file_size = config.get("max_file_size", 0)
            self._min_file_size = config.get("min_file_size", 0)
            self._enable_progress = config.get("enable_progress", True)
            self._max_workers = config.get("max_workers", 4)
            self._batch_size = config.get("batch_size", 100)
            
            # 事件触发配置
            trigger_events_config = config.get("trigger_events", [])
            if isinstance(trigger_events_config, list):
                # 直接处理列表格式（来自UI）
                self._trigger_events = [TriggerEvent(val) for val in trigger_events_config if self._is_valid_event(val)]
            elif isinstance(trigger_events_config, str) and trigger_events_config:
                # 处理字符串格式（向后兼容）
                event_values = self._parse_list(trigger_events_config)
                self._trigger_events = [TriggerEvent(val) for val in event_values if self._is_valid_event(val)]
            else:
                self._trigger_events = [TriggerEvent.TRANSFER_COMPLETE]
                
            # 事件过滤条件
            self._event_conditions = self._parse_event_conditions(config.get("event_conditions", ""))
            
            # 验证配置
            self._validate_config()

        # 初始化服务辅助类
        self._notification_helper = NotificationHelper()
        self._mediaserver_helper = MediaServerHelper()
        
        # 初始化缓存系统
        self._cache = TTLCache(region="transfersync", maxsize=256, ttl=1800)
        
        # 初始化线程池
        if self._enabled:
            self._executor = ThreadPoolExecutor(max_workers=self._max_workers, thread_name_prefix="sync-")
        
        # 初始化调度器
        if self._enabled and (self._enable_incremental or self._enable_full_sync):
            self._init_scheduler()
            
        # 注册事件监听器
        if self._enabled:
            self._register_event_listeners()

    def _parse_paths(self, paths_str: str) -> List[str]:
        """解析路径配置"""
        if isinstance(paths_str, str):
            return [p.strip() for p in paths_str.split('\n') if p.strip()]
        return paths_str or []

    def _parse_list(self, list_str: str, separator: str = ',') -> List[str]:
        """解析列表配置"""
        if isinstance(list_str, str):
            return [p.strip() for p in list_str.split(separator) if p.strip()]
        return list_str or []

    def _validate_config(self):
        """使用增强的配置验证器验证配置"""
        validator = ConfigValidator()
        
        # 验证路径配置
        validator.validate_paths(self._copy_paths)
        
        # 验证Cron表达式
        cron_expressions = {}
        if self._enable_incremental:
            cron_expressions["增量同步"] = self._incremental_cron
        if self._enable_full_sync:
            cron_expressions["全量同步"] = self._full_sync_cron
        validator.validate_cron_expressions(cron_expressions)
        
        # 验证数值范围配置
        numeric_configs = {
            "最大文件大小": (self._max_file_size, 0, None),
            "最小文件大小": (self._min_file_size, 0, None),
            "最大工作线程": (self._max_workers, 1, 32),
            "批次大小": (self._batch_size, 1, 10000),
            "最大目录深度": (self._max_depth, -1, None),
        }
        validator.validate_numeric_ranges(numeric_configs)
        
        # 验证文件大小逻辑关系
        if (self._max_file_size > 0 and self._min_file_size > 0 and 
            self._min_file_size > self._max_file_size):
            validator.errors.append("最小文件大小不能大于最大文件大小")
        
        # 验证正则表达式模式
        validator.validate_regex_patterns(self._exclude_patterns)
        
        # 验证文件扩展名
        validator.validate_file_extensions(self._file_filters)
        
        # 验证触发事件配置
        trigger_event_values = [e.value for e in self._trigger_events]
        validator.validate_trigger_events(trigger_event_values)
        
        # 验证同步策略和模式的兼容性
        self._validate_strategy_compatibility(validator)
        
        # 处理验证结果
        summary = validator.get_summary()
        if summary['error_count'] > 0:
            error_msg = "配置验证失败:\n" + "\n".join(f"❌ {error}" for error in summary['errors'])
            logger.error(error_msg)
            if self._enable_notifications:
                self._send_notification("配置错误", error_msg)
        
        if summary['warning_count'] > 0:
            warning_msg = "配置警告:\n" + "\n".join(f"⚠️ {warning}" for warning in summary['warnings'])
            logger.warning(warning_msg)
            if self._enable_notifications:
                self._send_notification("配置警告", warning_msg)
        
        # 存储验证结果供其他方法使用
        self._config_validation_summary = summary
        
    def _validate_strategy_compatibility(self, validator: ConfigValidator):
        """验证同步策略和模式的兼容性"""
        
        # 硬链接策略的特殊检查
        if self._sync_strategy == SyncStrategy.HARDLINK:
            # 检查所有目标路径是否在同一文件系统
            if len(self._copy_paths) > 1:
                try:
                    devices = set()
                    for path_str in self._copy_paths:
                        path = Path(path_str)
                        if path.exists():
                            devices.add(path.stat().st_dev)
                    
                    if len(devices) > 1:
                        validator.warnings.append("硬链接策略需要所有目标路径在同一文件系统，跨文件系统将自动降级为复制模式")
                        
                except Exception as e:
                    validator.warnings.append(f"无法检查文件系统兼容性: {str(e)}")
        
        # 队列模式的资源检查
        if self._sync_mode == SyncMode.QUEUE and self._max_workers > 8:
            validator.warnings.append("队列模式使用过多线程可能影响系统性能，建议设置为1-4个")
        
        # 批量模式的配置建议
        if self._sync_mode == SyncMode.BATCH:
            if self._batch_size > 1000:
                validator.warnings.append("批次大小过大可能消耗大量内存，建议设置为50-500")
            if self._max_workers > 16:
                validator.warnings.append("批量模式使用过多线程可能导致资源竞争，建议设置为2-8个")
        
        # 软链接在Windows下的兼容性警告
        if self._sync_strategy == SyncStrategy.SOFTLINK and os.name == 'nt':
            validator.warnings.append("Windows系统创建软链接可能需要管理员权限")
    
    def get_config_validation_summary(self) -> Dict[str, Any]:
        """获取配置验证摘要"""
        return getattr(self, '_config_validation_summary', {
            'valid': True, 'error_count': 0, 'warning_count': 0, 
            'errors': [], 'warnings': []
        })

    def _is_valid_event(self, event_value: str) -> bool:
        """验证事件值是否有效"""
        try:
            TriggerEvent(event_value)
            return True
        except ValueError:
            return False

    def _parse_event_conditions(self, conditions_str: str) -> Dict[str, Any]:
        """解析事件过滤条件"""
        conditions = {}
        if not conditions_str:
            return conditions
            
        try:
            # 支持JSON格式或简单的key=value格式
            if conditions_str.strip().startswith('{'):
                import json
                conditions = json.loads(conditions_str)
            else:
                # 简单格式：key=value,key2=value2
                for condition in conditions_str.split(','):
                    if '=' in condition:
                        key, value = condition.split('=', 1)
                        conditions[key.strip()] = value.strip()
        except Exception as e:
            logger.error(f"解析事件条件失败: {str(e)}")
            
        return conditions

    def _register_event_listeners(self):
        """注册事件监听器"""
        # 清除之前注册的监听器
        self._unregister_event_listeners()
        
        # 注册新的事件监听器
        for trigger_event in self._trigger_events:
            try:
                if trigger_event == TriggerEvent.TRANSFER_COMPLETE:
                    eventmanager.register(EventType.TransferComplete)(self._on_transfer_complete)
                elif trigger_event == TriggerEvent.DOWNLOAD_ADDED:
                    eventmanager.register(EventType.DownloadAdded)(self._on_download_added)
                elif trigger_event == TriggerEvent.SUBSCRIBE_COMPLETE:
                    eventmanager.register(EventType.SubscribeComplete)(self._on_subscribe_complete)
                elif trigger_event == TriggerEvent.METADATA_SCRAPE:
                    eventmanager.register(EventType.MetadataScrape)(self._on_metadata_scrape)
                elif trigger_event == TriggerEvent.WEBHOOK_MESSAGE:
                    eventmanager.register(EventType.WebhookMessage)(self._on_webhook_message)
                elif trigger_event == TriggerEvent.USER_MESSAGE:
                    eventmanager.register(EventType.UserMessage)(self._on_user_message)
                elif trigger_event == TriggerEvent.PLUGIN_TRIGGERED:
                    eventmanager.register(EventType.PluginTriggered)(self._on_plugin_triggered)
                    
                logger.debug(f"已注册事件监听器: {trigger_event.value}")
            except Exception as e:
                logger.error(f"注册事件监听器失败 {trigger_event.value}: {str(e)}")

    def _unregister_event_listeners(self):
        """注销事件监听器"""
        try:
            # 注销所有可能的事件监听器
            event_handlers = [
                (EventType.TransferComplete, self._on_transfer_complete),
                (EventType.DownloadAdded, self._on_download_added),
                (EventType.SubscribeComplete, self._on_subscribe_complete),
                (EventType.MetadataScrape, self._on_metadata_scrape),
                (EventType.WebhookMessage, self._on_webhook_message),
                (EventType.UserMessage, self._on_user_message),
                (EventType.PluginTriggered, self._on_plugin_triggered)
            ]
            
            for event_type, handler in event_handlers:
                try:
                    eventmanager.unregister(event_type, handler)
                except:
                    pass  # 忽略注销失败的错误
                    
        except Exception as e:
            logger.error(f"注销事件监听器失败: {str(e)}")

    def get_state(self) -> bool:
        """
        获取插件状态
        """
        return self._enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        定义远程控制命令
        """
        return [
            {
                "cmd": "/transfersync",
                "event": EventType.PluginAction,
                "desc": "整理后同步",
                "category": "文件管理",
                "data": {
                    "action": "transfersync"
                }
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """
        获取插件API
        """
        return [
            {
                "path": "/sync_now",
                "endpoint": self.sync_now,
                "methods": ["GET"],
                "summary": "立即同步",
                "description": "立即执行全量同步"
            },
            {
                "path": "/reset_event_stats",
                "endpoint": self.reset_event_statistics,
                "methods": ["POST"],
                "summary": "重置事件统计",
                "description": "重置所有事件统计数据"
            }
        ]

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册公共定时服务
        """
        services = []
        
        if self._enable_incremental:
            services.append({
                "id": "transfersync_incremental",
                "name": "整理后同步-增量同步",
                "trigger": CronTrigger.from_crontab(self._incremental_cron),
                "func": self._incremental_sync,
                "kwargs": {}
            })
        
        if self._enable_full_sync:
            services.append({
                "id": "transfersync_full",
                "name": "整理后同步-全量同步", 
                "trigger": CronTrigger.from_crontab(self._full_sync_cron),
                "func": self._full_sync,
                "kwargs": {}
            })
        
        return services

    def get_actions(self) -> List[Dict[str, Any]]:
        """
        获取插件工作流动作
        """
        return [
            {
                "id": "sync_single",
                "name": "同步单个文件/目录",
                "func": self.sync_single_action,
                "kwargs": {}
            },
            {
                "id": "sync_incremental",
                "name": "执行增量同步",
                "func": self.sync_incremental_action,
                "kwargs": {}
            },
            {
                "id": "sync_full",
                "name": "执行全量同步",
                "func": self.sync_full_action,
                "kwargs": {}
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                            'hint': '开启后插件将处于激活状态',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'copy_paths',
                                            'label': '同步目标路径',
                                            'placeholder': '输入要同步到的目标路径，每行一个\n示例：\n/mnt/backup1\n/mnt/backup2',
                                            'hint': '整理完成后将内容复制到这些路径，每行一个路径',
                                            'persistent-hint': True,
                                            'rows': 4
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable_incremental',
                                            'label': '启用增量同步',
                                            'hint': '定时增量同步新增或修改的文件',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'incremental_cron',
                                            'label': '增量同步时间',
                                            'placeholder': '0 */6 * * *',
                                            'hint': '使用Cron表达式，默认每6小时执行一次',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable_full_sync',
                                            'label': '启用全量同步',
                                            'hint': '定时全量同步所有整理后的文件',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'full_sync_cron',
                                            'label': '全量同步时间',
                                            'placeholder': '0 2 * * 0',
                                            'hint': '使用Cron表达式，默认每周日凌晨2点执行',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable_notifications',
                                            'label': '启用通知推送',
                                            'hint': '同步完成后发送通知消息',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'notification_channels',
                                            'label': '通知渠道',
                                            'placeholder': '渠道1,渠道2',
                                            'hint': '指定发送通知的渠道，多个用逗号分隔，留空则发送到所有渠道',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VDivider',
                        'props': {
                            'class': 'my-4'
                        }
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSubheader',
                                        'props': {
                                            'text': '高级配置'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'sync_strategy',
                                            'label': '同步策略',
                                            'items': [
                                                {'title': '复制', 'value': 'copy'},
                                                {'title': '移动', 'value': 'move'},
                                                {'title': '硬链接', 'value': 'hardlink'},
                                                {'title': '软链接', 'value': 'softlink'}
                                            ],
                                            'hint': '选择文件同步方式',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'sync_mode',
                                            'label': '同步模式',
                                            'items': [
                                                {'title': '立即同步', 'value': 'immediate'},
                                                {'title': '批量同步', 'value': 'batch'},
                                                {'title': '队列同步', 'value': 'queue'}
                                            ],
                                            'hint': '选择同步执行方式',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'max_depth',
                                            'label': '最大目录深度',
                                            'type': 'number',
                                            'placeholder': '-1',
                                            'hint': '-1表示无限制深度',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'max_workers',
                                            'label': '最大工作线程',
                                            'type': 'number',
                                            'placeholder': '4',
                                            'hint': '并发同步的线程数',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 4
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'batch_size',
                                            'label': '批次大小',
                                            'type': 'number',
                                            'placeholder': '100',
                                            'hint': '批量处理的文件数量',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'min_file_size',
                                            'label': '最小文件大小(MB)',
                                            'type': 'number',
                                            'placeholder': '0',
                                            'hint': '0表示无限制，仅同步大于此大小的文件',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'max_file_size',
                                            'label': '最大文件大小(MB)',
                                            'type': 'number',
                                            'placeholder': '0',
                                            'hint': '0表示无限制，仅同步小于此大小的文件',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'file_filters',
                                            'label': '文件类型过滤',
                                            'placeholder': '.mp4,.mkv,.avi',
                                            'hint': '仅同步指定扩展名的文件，多个用逗号分隔',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable_progress',
                                            'label': '启用进度跟踪',
                                            'hint': '显示同步进度信息',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'exclude_patterns',
                                            'label': '排除模式',
                                            'placeholder': '.*\\.tmp$\n.*temp.*\n.*cache.*',
                                            'hint': '使用正则表达式排除不需要同步的文件，每行一个模式',
                                            'persistent-hint': True,
                                            'rows': 3
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VDivider',
                        'props': {
                            'class': 'my-4'
                        }
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSubheader',
                                        'props': {
                                            'text': '触发事件配置'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'trigger_events',
                                            'label': '触发事件类型',
                                            'items': [
                                                {'title': '整理完成事件', 'value': 'transfer.complete'},
                                                {'title': '下载添加事件', 'value': 'download.added'},
                                                {'title': '订阅完成事件', 'value': 'subscribe.complete'},
                                                {'title': '媒体添加事件', 'value': 'media.added'},
                                                {'title': '文件移动事件', 'value': 'file.moved'},
                                                {'title': '目录扫描完成事件', 'value': 'directory.scan.complete'},
                                                {'title': '刮削完成事件', 'value': 'scrape.complete'},
                                                {'title': '插件触发事件', 'value': 'plugin.triggered'}
                                            ],
                                            'multiple': True,
                                            'chips': True,
                                            'hint': '选择要监听的事件类型，可选择多个',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12
                                },
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'event_conditions',
                                            'label': '事件过滤条件',
                                            'placeholder': 'media_type=movie\nquality=>=1080p\nsize_mb=>=100',
                                            'hint': '设置事件过滤条件，每行一个条件。格式：属性名=条件值\n支持的条件：media_type, quality, size_mb, path, name等',
                                            'persistent-hint': True,
                                            'rows': 3
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VExpansionPanels',
                        'props': {
                            'class': 'mt-4'
                        },
                        'content': [
                            {
                                'component': 'VExpansionPanel',
                                'content': [
                                    {
                                        'component': 'VExpansionPanelTitle',
                                        'text': '📖 配置指南和最佳实践'
                                    },
                                    {
                                        'component': 'VExpansionPanelText',
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'mb-3'
                                                },
                                                'text': '🎯 **同步策略选择建议:**'
                                            },
                                            {
                                                'component': 'VList',
                                                'props': {
                                                    'density': 'compact'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '复制模式：适合跨磁盘备份，保留原文件'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '移动模式：适合整理归档，转移文件位置'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '硬链接：节省空间，需同一文件系统'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '软链接：创建快捷方式，灵活引用'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'mb-3 mt-4'
                                                },
                                                'text': '⚙️ **性能优化建议:**'
                                            },
                                            {
                                                'component': 'VList',
                                                'props': {
                                                    'density': 'compact'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '立即模式：适合少量大文件，实时响应'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '批量模式：适合大量文件，提升效率'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '队列模式：后台处理，不阻塞主进程'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'mb-3 mt-4'
                                                },
                                                'text': '🎯 **事件触发配置:**'
                                            },
                                            {
                                                'component': 'VList',
                                                'props': {
                                                    'density': 'compact'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '支持多事件类型并行监听，可根据需要选择'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '事件过滤条件支持复杂匹配，格式：属性名=值'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'content': [
                                                            {
                                                                'component': 'VListItemTitle',
                                                                'text': '支持数值比较：>=, <=, >, <, != 操作符'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VAlert',
                        'props': {
                            'type': 'success',
                            'variant': 'tonal',
                            'class': 'mt-4',
                            'text': '✨ 插件支持实时配置验证、性能监控、统计分析等企业级功能，助您构建稳定可靠的同步系统。'
                        }
                    }
                ]
            }
        ], {
            "enabled": False,
            "copy_paths": "",
            "enable_incremental": False,
            "incremental_cron": "0 */6 * * *",
            "enable_full_sync": False,
            "full_sync_cron": "0 2 * * 0",
            "enable_notifications": False,
            "notification_channels": "",
            "sync_strategy": "copy",
            "sync_mode": "immediate",
            "max_depth": -1,
            "file_filters": "",
            "exclude_patterns": "",
            "min_file_size": 0,
            "max_file_size": 0,
            "enable_progress": True,
            "max_workers": 4,
            "batch_size": 100,
            "trigger_events": ["transfer.complete"],
            "event_conditions": ""
        }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        """
        # 获取同步记录和状态信息
        sync_status = self._get_sync_status()
        
        return [
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 4
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'tonal'
                                },
                                'content': [
                                    {
                                        'component': 'VCardText',
                                        'props': {
                                            'class': 'text-center'
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-h4 font-weight-bold'
                                                },
                                                'text': str(sync_status.get('total_synced', 0))
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-caption'
                                                },
                                                'text': '已同步文件数'
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-body-2 mt-1'
                                                },
                                                'text': sync_status.get('total_size', '0 B')
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 4
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'tonal'
                                },
                                'content': [
                                    {
                                        'component': 'VCardText',
                                        'props': {
                                            'class': 'text-center'
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-h6 font-weight-bold'
                                                },
                                                'text': sync_status.get('last_sync_time', '未执行')
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-caption'
                                                },
                                                'text': '最后同步时间'
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 4
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'tonal'
                                },
                                'content': [
                                    {
                                        'component': 'VCardText',
                                        'props': {
                                            'class': 'text-center'
                                        },
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-h6 font-weight-bold'
                                                },
                                                'text': '正常' if self._enabled else '已停用'
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-caption'
                                                },
                                                'text': '同步状态'
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'title': '同步配置'
                                },
                                'content': [
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VList',
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'props': {
                                                            'title': '目标路径',
                                                            'subtitle': '\n'.join(self._copy_paths) if self._copy_paths else '未配置'
                                                        }
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'props': {
                                                            'title': '增量同步',
                                                            'subtitle': f'{"已启用" if self._enable_incremental else "已停用"} - {self._incremental_cron}'
                                                        }
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'props': {
                                                            'title': '全量同步',
                                                            'subtitle': f'{"已启用" if self._enable_full_sync else "已停用"} - {self._full_sync_cron}'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'title': '同步进度'
                                },
                                'content': [
                                    {
                                        'component': 'VCardText',
                                        'content': self._generate_progress_components(sync_status)
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 6
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'outlined',
                                    'class': 'mt-4'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'text-h6'
                                        },
                                        'text': '配置验证状态'
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': self._generate_config_validation_components()
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 6
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'outlined',
                                    'class': 'mt-4'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'text-h6'
                                        },
                                        'text': '事件统计监控'
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': self._generate_event_stats_components()
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def _generate_config_validation_components(self) -> List[dict]:
        """生成配置验证状态显示组件"""
        components = []
        validation_summary = self.get_config_validation_summary()
        
        # 总体状态卡片
        status_color = 'success' if validation_summary['valid'] else 'error'
        status_text = '✅ 配置正常' if validation_summary['valid'] else '❌ 配置异常'
        
        components.append({
            'component': 'VChip',
            'props': {
                'color': status_color,
                'variant': 'tonal',
                'class': 'mb-3'
            },
            'text': status_text
        })
        
        # 统计信息
        stats_items = [
            f"错误: {validation_summary['error_count']}",
            f"警告: {validation_summary['warning_count']}"
        ]
        
        components.append({
            'component': 'VRow',
            'content': [
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 6
                    },
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'text-caption'
                            },
                            'text': stats_items[0]
                        }
                    ]
                },
                {
                    'component': 'VCol',
                    'props': {
                        'cols': 6
                    },
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'text-caption'
                            },
                            'text': stats_items[1]
                        }
                    ]
                }
            ]
        })
        
        # 错误列表
        if validation_summary['error_count'] > 0:
            components.append({
                'component': 'VDivider',
                'props': {
                    'class': 'my-2'
                }
            })
            
            components.append({
                'component': 'div',
                'props': {
                    'class': 'text-body-2 font-weight-bold mb-2'
                },
                'text': '配置错误:'
            })
            
            for error in validation_summary['errors']:
                components.append({
                    'component': 'VAlert',
                    'props': {
                        'type': 'error',
                        'density': 'compact',
                        'variant': 'tonal',
                        'class': 'mb-2'
                    },
                    'text': error
                })
        
        # 警告列表  
        if validation_summary['warning_count'] > 0:
            components.append({
                'component': 'VDivider',
                'props': {
                    'class': 'my-2'
                }
            })
            
            components.append({
                'component': 'div',
                'props': {
                    'class': 'text-body-2 font-weight-bold mb-2'
                },
                'text': '配置警告:'
            })
            
            for warning in validation_summary['warnings']:
                components.append({
                    'component': 'VAlert',
                    'props': {
                        'type': 'warning',
                        'density': 'compact',
                        'variant': 'tonal',
                        'class': 'mb-2'
                    },
                    'text': warning
                })
        
        # 如果没有问题，显示成功信息
        if validation_summary['valid'] and validation_summary['warning_count'] == 0:
            components.append({
                'component': 'div',
                'props': {
                    'class': 'text-success text-center mt-3'
                },
                'text': '所有配置项验证通过 🎉'
            })
        
        return components

    def _generate_event_stats_components(self) -> List[dict]:
        """生成事件统计显示组件"""
        components = []
        event_stats = self.get_event_statistics()
        
        if not event_stats:
            # 如果没有统计数据，显示提示信息
            components.append({
                'component': 'VAlert',
                'props': {
                    'type': 'info',
                    'variant': 'tonal'
                },
                'text': '暂无事件统计数据，配置并启用插件后将开始收集统计信息。'
            })
        else:
            # 创建统计数据表格
            components.append({
                'component': 'VDataTable',
                'props': {
                    'headers': [
                        {'title': '事件类型', 'key': 'event_type'},
                        {'title': '总次数', 'key': 'total_count'},
                        {'title': '成功次数', 'key': 'success_count'},
                        {'title': '失败次数', 'key': 'failure_count'},
                        {'title': '成功率', 'key': 'success_rate'},
                        {'title': '最后触发', 'key': 'last_triggered'},
                    ],
                    'items': [
                        {
                            'event_type': self._get_event_display_name(event_key),
                            'total_count': stats['total_count'],
                            'success_count': stats['success_count'],
                            'failure_count': stats['failure_count'],
                            'success_rate': f"{stats.get('success_rate', 0)}%",
                            'last_triggered': stats.get('last_triggered', '从未触发'),
                        }
                        for event_key, stats in event_stats.items()
                    ],
                    'density': 'compact',
                    'hide-default-footer': True
                }
            })
            
            # 添加重置按钮
            components.append({
                'component': 'VRow',
                'props': {
                    'class': 'mt-3'
                },
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'class': 'text-right'
                        },
                        'content': [
                            {
                                'component': 'VBtn',
                                'props': {
                                    'size': 'small',
                                    'variant': 'outlined',
                                    'color': 'warning',
                                    'onclick': 'reset_event_stats'
                                },
                                'text': '重置统计'
                            }
                        ]
                    }
                ]
            })
        
        return components

    def _get_event_display_name(self, event_key: str) -> str:
        """获取事件类型的显示名称"""
        event_names = {
            'transfer.complete': '整理完成事件',
            'download.added': '下载添加事件',
            'subscribe.complete': '订阅完成事件',
            'media.added': '媒体添加事件',
            'file.moved': '文件移动事件',
            'directory.scan.complete': '目录扫描完成事件',
            'scrape.complete': '刮削完成事件',
            'plugin.triggered': '插件触发事件'
        }
        return event_names.get(event_key, event_key)

    def _generate_progress_components(self, sync_status: Dict) -> List[dict]:
        """生成进度显示组件"""
        components = []
        
        if not self._enable_progress or not self._sync_progress:
            components.append({
                'component': 'VAlert',
                'props': {
                    'type': 'info',
                    'variant': 'tonal',
                    'text': '当前没有进行中的同步任务'
                }
            })
            return components
            
        with self._progress_lock:
            for task_id, progress in self._sync_progress.items():
                if progress['status'] == SyncStatus.COMPLETED.value:
                    continue
                    
                components.append({
                    'component': 'div',
                    'props': {
                        'class': 'mb-4'
                    },
                    'content': [
                        {
                            'component': 'div',
                            'props': {
                                'class': 'd-flex justify-space-between mb-2'
                            },
                            'content': [
                                {
                                    'component': 'span',
                                    'props': {
                                        'class': 'text-subtitle-2'
                                    },
                                    'text': f'任务: {task_id}'
                                },
                                {
                                    'component': 'span',
                                    'props': {
                                        'class': 'text-caption'
                                    },
                                    'text': f"{progress['current']}/{progress['total']} ({progress['percentage']:.1f}%)"
                                }
                            ]
                        },
                        {
                            'component': 'VProgressLinear',
                            'props': {
                                'model-value': progress['percentage'],
                                'color': 'primary',
                                'height': '8'
                            }
                        },
                        {
                            'component': 'div',
                            'props': {
                                'class': 'text-caption mt-1'
                            },
                            'text': f"状态: {progress['status']} | 速度: {progress['speed']:.1f} 文件/秒"
                        }
                    ]
                })
                
        return components if components else [{
            'component': 'VAlert',
            'props': {
                'type': 'success',
                'variant': 'tonal', 
                'text': '所有同步任务已完成'
            }
        }]

    def _should_process_event(self, event: Event, event_type: TriggerEvent) -> Tuple[bool, str]:
        """判断是否应该处理事件"""
        try:
            event_data = event.event_data
            if not event_data:
                return False, "事件数据为空"
                
            # 记录事件统计
            self._record_event_statistics(event_type, event_data)
            
            # 检查事件过滤条件
            if not self._check_event_conditions(event_data, event_type):
                return False, "事件不满足过滤条件"
                
            return True, "事件通过验证"
            
        except Exception as e:
            logger.error(f"事件验证失败: {str(e)}")
            return False, f"事件验证异常: {str(e)}"

    def _check_event_conditions(self, event_data: Dict, event_type: TriggerEvent) -> bool:
        """检查事件是否满足过滤条件"""
        if not self._event_conditions:
            return True
            
        try:
            # 媒体类型过滤
            if EventCondition.MEDIA_TYPE.value in self._event_conditions:
                expected_type = self._event_conditions[EventCondition.MEDIA_TYPE.value].lower()
                actual_type = str(event_data.get('type', '')).lower()
                if expected_type != actual_type and expected_type != 'all':
                    return False
                    
            # 源路径过滤
            if EventCondition.SOURCE_PATH.value in self._event_conditions:
                pattern = self._event_conditions[EventCondition.SOURCE_PATH.value]
                source_path = str(event_data.get('src', ''))
                if not re.search(pattern, source_path, re.IGNORECASE):
                    return False
                    
            # 目标路径过滤
            if EventCondition.TARGET_PATH.value in self._event_conditions:
                pattern = self._event_conditions[EventCondition.TARGET_PATH.value]
                target_path = str(event_data.get('dest', ''))
                if not re.search(pattern, target_path, re.IGNORECASE):
                    return False
                    
            return True
            
        except Exception as e:
            logger.error(f"检查事件条件失败: {str(e)}")
            return True  # 条件检查失败时允许通过

    def _record_event_statistics(self, event_type: TriggerEvent, event_data: Dict):
        """记录事件统计信息"""
        try:
            event_key = event_type.value
            
            if event_key not in self._event_statistics:
                self._event_statistics[event_key] = {
                    'total_count': 0,
                    'processed_count': 0,
                    'last_event_time': None,
                    'last_event_data': {}
                }
                
            self._event_statistics[event_key]['total_count'] += 1
            self._event_statistics[event_key]['last_event_time'] = datetime.now()
            self._event_statistics[event_key]['last_event_data'] = event_data.copy()
            
        except Exception as e:
            logger.error(f"记录事件统计失败: {str(e)}")

    def _extract_sync_path(self, event_data: Dict, event_type: TriggerEvent) -> Optional[str]:
        """从事件数据中提取需要同步的路径"""
        try:
            if event_type == TriggerEvent.TRANSFER_COMPLETE:
                return event_data.get("dest")
            elif event_type == TriggerEvent.DOWNLOAD_ADDED:
                return event_data.get("path") or event_data.get("save_path")
            elif event_type == TriggerEvent.SUBSCRIBE_COMPLETE:
                return event_data.get("path") or event_data.get("dest")
            elif event_type == TriggerEvent.METADATA_SCRAPE:
                return event_data.get("path") or event_data.get("file_path")
            else:
                # 通用路径提取逻辑
                return event_data.get("path") or event_data.get("dest") or event_data.get("src")
                
        except Exception as e:
            logger.error(f"提取同步路径失败: {str(e)}")
            return None

    def _on_transfer_complete(self, event: Event):
        """处理整理完成事件"""
        should_process, reason = self._should_process_event(event, TriggerEvent.TRANSFER_COMPLETE)
        if not should_process:
            logger.debug(f"跳过整理完成事件: {reason}")
            return
            
        try:
            event_data = event.event_data
            sync_path = self._extract_sync_path(event_data, TriggerEvent.TRANSFER_COMPLETE)
            
            if sync_path:
                logger.info(f"监听到整理完成事件: {event_data.get('src')} -> {sync_path}")
                self._sync_single_item(sync_path)
                self._send_notification(
                    title="自动同步完成",
                    text=f"已自动同步整理后的内容: {Path(sync_path).name}"
                )
                # 更新处理统计
                self._event_statistics[TriggerEvent.TRANSFER_COMPLETE.value]['processed_count'] += 1
            else:
                logger.warning("整理完成事件中没有有效的路径信息")
                
        except Exception as e:
            logger.error(f"处理整理完成事件时发生错误: {str(e)}")

    def _on_download_added(self, event: Event):
        """处理下载添加事件"""
        should_process, reason = self._should_process_event(event, TriggerEvent.DOWNLOAD_ADDED)
        if not should_process:
            logger.debug(f"跳过下载添加事件: {reason}")
            return
            
        try:
            event_data = event.event_data
            sync_path = self._extract_sync_path(event_data, TriggerEvent.DOWNLOAD_ADDED)
            
            if sync_path:
                logger.info(f"监听到下载添加事件: {sync_path}")
                self._sync_single_item(sync_path)
                self._send_notification(
                    title="下载同步完成",
                    text=f"已同步新下载的内容: {Path(sync_path).name}"
                )
                self._event_statistics[TriggerEvent.DOWNLOAD_ADDED.value]['processed_count'] += 1
            else:
                logger.warning("下载添加事件中没有有效的路径信息")
                
        except Exception as e:
            logger.error(f"处理下载添加事件时发生错误: {str(e)}")

    def _on_subscribe_complete(self, event: Event):
        """处理订阅完成事件"""
        should_process, reason = self._should_process_event(event, TriggerEvent.SUBSCRIBE_COMPLETE)
        if not should_process:
            logger.debug(f"跳过订阅完成事件: {reason}")
            return
            
        try:
            event_data = event.event_data
            sync_path = self._extract_sync_path(event_data, TriggerEvent.SUBSCRIBE_COMPLETE)
            
            if sync_path:
                logger.info(f"监听到订阅完成事件: {sync_path}")
                self._sync_single_item(sync_path)
                self._send_notification(
                    title="订阅同步完成",
                    text=f"已同步订阅完成的内容: {Path(sync_path).name}"
                )
                self._event_statistics[TriggerEvent.SUBSCRIBE_COMPLETE.value]['processed_count'] += 1
            else:
                logger.warning("订阅完成事件中没有有效的路径信息")
                
        except Exception as e:
            logger.error(f"处理订阅完成事件时发生错误: {str(e)}")

    def _on_metadata_scrape(self, event: Event):
        """处理元数据刮削事件"""
        should_process, reason = self._should_process_event(event, TriggerEvent.METADATA_SCRAPE)
        if not should_process:
            logger.debug(f"跳过元数据刮削事件: {reason}")
            return
            
        try:
            event_data = event.event_data
            sync_path = self._extract_sync_path(event_data, TriggerEvent.METADATA_SCRAPE)
            
            if sync_path:
                logger.info(f"监听到元数据刮削事件: {sync_path}")
                self._sync_single_item(sync_path)
                self._send_notification(
                    title="刮削同步完成",
                    text=f"已同步刮削完成的内容: {Path(sync_path).name}"
                )
                self._event_statistics[TriggerEvent.METADATA_SCRAPE.value]['processed_count'] += 1
            else:
                logger.warning("元数据刮削事件中没有有效的路径信息")
                
        except Exception as e:
            logger.error(f"处理元数据刮削事件时发生错误: {str(e)}")

    def _on_webhook_message(self, event: Event):
        """处理Webhook消息事件"""
        should_process, reason = self._should_process_event(event, TriggerEvent.WEBHOOK_MESSAGE)
        if not should_process:
            logger.debug(f"跳过Webhook消息事件: {reason}")
            return
            
        try:
            event_data = event.event_data
            sync_path = self._extract_sync_path(event_data, TriggerEvent.WEBHOOK_MESSAGE)
            
            if sync_path:
                logger.info(f"监听到Webhook消息事件: {sync_path}")
                self._sync_single_item(sync_path)
                self._send_notification(
                    title="Webhook同步完成",
                    text=f"已同步Webhook触发的内容: {Path(sync_path).name}"
                )
                self._event_statistics[TriggerEvent.WEBHOOK_MESSAGE.value]['processed_count'] += 1
            else:
                logger.warning("Webhook消息事件中没有有效的路径信息")
                
        except Exception as e:
            logger.error(f"处理Webhook消息事件时发生错误: {str(e)}")

    def _on_user_message(self, event: Event):
        """处理用户消息事件"""
        should_process, reason = self._should_process_event(event, TriggerEvent.USER_MESSAGE)
        if not should_process:
            logger.debug(f"跳过用户消息事件: {reason}")
            return
            
        try:
            event_data = event.event_data
            sync_path = self._extract_sync_path(event_data, TriggerEvent.USER_MESSAGE)
            
            if sync_path:
                logger.info(f"监听到用户消息事件: {sync_path}")
                self._sync_single_item(sync_path)
                self._send_notification(
                    title="用户消息同步完成",
                    text=f"已同步用户触发的内容: {Path(sync_path).name}"
                )
                self._event_statistics[TriggerEvent.USER_MESSAGE.value]['processed_count'] += 1
            else:
                logger.warning("用户消息事件中没有有效的路径信息")
                
        except Exception as e:
            logger.error(f"处理用户消息事件时发生错误: {str(e)}")

    def _on_plugin_triggered(self, event: Event):
        """处理插件触发事件"""
        should_process, reason = self._should_process_event(event, TriggerEvent.PLUGIN_TRIGGERED)
        if not should_process:
            logger.debug(f"跳过插件触发事件: {reason}")
            return
            
        try:
            event_data = event.event_data
            sync_path = self._extract_sync_path(event_data, TriggerEvent.PLUGIN_TRIGGERED)
            
            if sync_path:
                logger.info(f"监听到插件触发事件: {sync_path}")
                self._sync_single_item(sync_path)
                self._send_notification(
                    title="插件触发同步完成",
                    text=f"已同步插件触发的内容: {Path(sync_path).name}"
                )
                self._event_statistics[TriggerEvent.PLUGIN_TRIGGERED.value]['processed_count'] += 1
            else:
                logger.warning("插件触发事件中没有有效的路径信息")
                
        except Exception as e:
            logger.error(f"处理插件触发事件时发生错误: {str(e)}")

    # 保留原有的方法作为兼容
    def on_transfer_complete(self, event: Event):
        """
        监听整理完成事件
        """
        if not self._enabled:
            return
        
        try:
            event_data = event.event_data
            if not event_data:
                return
            
            # 获取整理完成的文件信息
            src_path = event_data.get("src")
            dest_path = event_data.get("dest")
            
            if not dest_path:
                logger.warning("整理事件中没有目标路径信息")
                return
            
            logger.info(f"监听到整理完成事件: {src_path} -> {dest_path}")
            
            # 立即同步新整理的内容
            self._sync_single_item(dest_path)
            
            # 发送通知
            self._send_notification(
                title="自动同步完成",
                text=f"已自动同步整理后的内容: {Path(dest_path).name}"
            )
            
        except Exception as e:
            logger.error(f"处理整理完成事件时发生错误: {str(e)}")

    @eventmanager.register(EventType.PluginAction)
    def on_plugin_action(self, event: Event):
        """
        监听插件动作事件
        """
        event_data = event.event_data
        if not event_data or event_data.get("action") != "transfersync":
            return
        
        # 执行立即同步
        self.sync_now()

    def _init_scheduler(self):
        """
        初始化调度器
        """
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
        
        self._scheduler = BackgroundScheduler()
        
        # 添加增量同步任务
        if self._enable_incremental:
            try:
                self._scheduler.add_job(
                    self._incremental_sync,
                    CronTrigger.from_crontab(self._incremental_cron),
                    id="incremental_sync",
                    name="增量同步"
                )
                logger.info(f"增量同步任务已添加: {self._incremental_cron}")
            except Exception as e:
                logger.error(f"添加增量同步任务失败: {str(e)}")
        
        # 添加全量同步任务
        if self._enable_full_sync:
            try:
                self._scheduler.add_job(
                    self._full_sync,
                    CronTrigger.from_crontab(self._full_sync_cron),
                    id="full_sync",
                    name="全量同步"
                )
                logger.info(f"全量同步任务已添加: {self._full_sync_cron}")
            except Exception as e:
                logger.error(f"添加全量同步任务失败: {str(e)}")
        
        # 启动调度器
        if self._scheduler.get_jobs():
            self._scheduler.start()
            logger.info("同步调度器已启动")

    def _sync_single_item(self, source_path: str):
        """
        同步单个文件或目录
        """
        if not self._copy_paths:
            logger.warning("未配置同步目标路径")
            return
        
        try:
            source = Path(source_path)
            if not source.exists():
                logger.warning(f"源路径不存在: {source_path}")
                return

            # 生成任务ID
            task_id = f"sync_{int(time.time())}"
            
            # 获取需要同步的文件列表
            if source.is_file():
                files_to_sync = [source] if self._should_sync_file(source) else []
            else:
                files_to_sync = self._get_filtered_files(source, self._max_depth)
            
            if not files_to_sync:
                logger.info(f"没有符合条件的文件需要同步: {source_path}")
                return
                
            total_files = len(files_to_sync)
            self._update_progress(task_id, 0, total_files, SyncStatus.RUNNING.value)
            
            successful_syncs = 0
            
            for target_path in self._copy_paths:
                target = Path(target_path)
                if not target.exists():
                    logger.warning(f"目标路径不存在: {target_path}")
                    continue
                
                try:
                    if source.is_file():
                        # 单文件同步
                        target_full = target / source.name
                        if self._execute_sync_strategy(source, target_full):
                            successful_syncs += 1
                            logger.info(f"文件已同步: {source} -> {target_full}")
                    else:
                        # 目录同步
                        if self._sync_mode == SyncMode.BATCH:
                            # 批量模式
                            self._sync_directory_batch(source, target, files_to_sync, task_id)
                        elif self._sync_mode == SyncMode.QUEUE:
                            # 队列模式
                            self._sync_directory_queue(source, target, files_to_sync, task_id)
                        else:
                            # 立即模式
                            self._sync_directory_immediate(source, target, files_to_sync, task_id)
                        successful_syncs += 1
                    
                except Exception as e:
                    logger.error(f"同步到 {target_path} 失败: {str(e)}")
                    
            # 记录同步信息
            if successful_syncs > 0:
                with self._lock:
                    self._sync_records[str(source)] = {
                        'sync_time': datetime.now(),
                        'target_paths': self._copy_paths.copy(),
                        'file_count': total_files,
                        'successful_targets': successful_syncs
                    }
                    
            self._update_progress(task_id, total_files, total_files, SyncStatus.COMPLETED.value)
                    
        except Exception as e:
            logger.error(f"同步单个项目失败: {str(e)}")
            if 'task_id' in locals():
                self._update_progress(task_id, 0, 0, SyncStatus.ERROR.value)

    def _sync_directory_immediate(self, source: Path, target_base: Path, files: List[Path], task_id: str):
        """立即同步目录"""
        processed = 0
        for file_path in files:
            try:
                rel_path = file_path.relative_to(source)
                target_file = target_base / source.name / rel_path
                
                if self._execute_sync_strategy(file_path, target_file):
                    processed += 1
                    self._update_progress(task_id, processed, len(files))
                    
            except Exception as e:
                logger.error(f"同步文件失败 {file_path}: {str(e)}")

    def _sync_directory_batch(self, source: Path, target_base: Path, files: List[Path], task_id: str):
        """批量同步目录"""
        processed = 0
        
        def process_batch(batch_files):
            nonlocal processed
            if self._executor:
                futures = []
                for file_path in batch_files:
                    rel_path = file_path.relative_to(source)
                    target_file = target_base / source.name / rel_path
                    future = self._executor.submit(self._execute_sync_strategy, file_path, target_file)
                    futures.append(future)
                
                for future in futures:
                    try:
                        if future.result():
                            processed += 1
                            self._update_progress(task_id, processed, len(files))
                    except Exception as e:
                        logger.error(f"批量同步失败: {str(e)}")
                        
        # 按批次处理
        for i in range(0, len(files), self._batch_size):
            batch = files[i:i + self._batch_size]
            process_batch(batch)

    def _sync_directory_queue(self, source: Path, target_base: Path, files: List[Path], task_id: str):
        """队列同步目录"""
        # 将文件添加到同步队列
        for file_path in files:
            rel_path = file_path.relative_to(source)
            target_file = target_base / source.name / rel_path
            
            sync_item = {
                'source': file_path,
                'target': target_file,
                'task_id': task_id,
                'timestamp': datetime.now()
            }
            
            with self._lock:
                self._sync_queue.append(sync_item)
                
        # 启动队列处理（如果尚未启动）
        self._process_sync_queue()

    def _should_sync_file(self, file_path: Path) -> bool:
        """判断文件是否应该被同步"""
        try:
            # 检查文件大小
            if file_path.is_file():
                file_size_mb = file_path.stat().st_size / (1024 * 1024)
                
                if self._min_file_size > 0 and file_size_mb < self._min_file_size:
                    return False
                    
                if self._max_file_size > 0 and file_size_mb > self._max_file_size:
                    return False
            
            # 检查文件扩展名过滤
            if self._file_filters:
                file_extension = file_path.suffix.lower()
                if not any(filter_ext.lower() in file_extension for filter_ext in self._file_filters):
                    return False
            
            # 检查排除模式
            file_str = str(file_path)
            for pattern in self._exclude_patterns:
                if re.search(pattern, file_str, re.IGNORECASE):
                    return False
                    
            return True
            
        except Exception as e:
            logger.debug(f"文件过滤检查失败 {file_path}: {str(e)}")
            return True

    def _execute_sync_strategy(self, source: Path, target: Path) -> bool:
        """执行同步策略，使用原子性操作"""
        # 验证磁盘空间
        if not self._check_disk_space(source, target.parent):
            raise SyncSpaceError(f"目标路径磁盘空间不足: {target.parent}")
        
        # 验证权限
        if not self._check_permissions(target.parent):
            raise SyncPermissionError(f"目标路径权限不足: {target.parent}")
        
        with AtomicFileOperation() as atomic_op:
            if self._sync_strategy == SyncStrategy.COPY:
                return self._execute_copy(source, target, atomic_op)
            elif self._sync_strategy == SyncStrategy.MOVE:
                return self._execute_move(source, target, atomic_op)
            elif self._sync_strategy == SyncStrategy.HARDLINK:
                return self._execute_hardlink(source, target, atomic_op)
            elif self._sync_strategy == SyncStrategy.SOFTLINK:
                return self._execute_softlink(source, target, atomic_op)
            else:
                logger.error(f"不支持的同步策略: {self._sync_strategy}")
                return False
    def _check_disk_space(self, source: Path, target_parent: Path) -> bool:
        """检查磁盘空间是否足够"""
        try:
            if source.is_file():
                required_space = source.stat().st_size
            else:
                # 计算目录大小
                required_space = sum(f.stat().st_size for f in source.rglob('*') if f.is_file())
            
            # 增加10%的缓冲空间
            required_space = int(required_space * 1.1)
            
            # 获取可用空间（带缓存）
            available_space = self._get_available_space_cached(target_parent)
            
            return available_space > required_space
            
        except Exception as e:
            logger.warning(f"磁盘空间检查失败，继续操作: {str(e)}")
            return True  # 检查失败时允许继续操作

    @cached(region="transfersync", ttl=300)  # 5分钟缓存
    def _get_available_space_cached(self, path: Path) -> int:
        """获取目录可用空间（缓存5分钟）"""
        return shutil.disk_usage(path).free

    def _check_permissions(self, target_parent: Path) -> bool:
        """检查目录权限"""
        try:
            target_parent.mkdir(parents=True, exist_ok=True)
            # 尝试创建临时文件来测试写权限
            temp_file = target_parent / f".temp_permission_test_{os.getpid()}"
            try:
                temp_file.touch()
                temp_file.unlink()
                return True
            except Exception:
                return False
        except Exception as e:
            logger.debug(f"权限检查失败 {target_parent}: {str(e)}")
            return False

    def _execute_copy(self, source: Path, target: Path, atomic_op: AtomicFileOperation) -> bool:
        """执行复制操作"""
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_op.track_created_dir(target.parent)
            shutil.copy2(source, target)
            atomic_op.track_created_file(target)
        else:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target, copy_function=shutil.copy2)
            atomic_op.track_created_dir(target)
        return True

    def _execute_move(self, source: Path, target: Path, atomic_op: AtomicFileOperation) -> bool:
        """执行移动操作"""
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_op.track_created_dir(target.parent)
        shutil.move(str(source), str(target))
        if target.is_file():
            atomic_op.track_created_file(target)
        else:
            atomic_op.track_created_dir(target)
        return True

    def _execute_hardlink(self, source: Path, target: Path, atomic_op: AtomicFileOperation) -> bool:
        """执行硬链接操作"""
        if source.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            atomic_op.track_created_dir(target.parent)
            if target.exists():
                target.unlink()
            target.hardlink_to(source)
            atomic_op.track_created_file(target)
        else:
            # 对目录中的每个文件创建硬链接
            target.mkdir(parents=True, exist_ok=True)
            atomic_op.track_created_dir(target)
            for item in source.rglob('*'):
                if item.is_file():
                    rel_path = item.relative_to(source)
                    target_item = target / rel_path
                    target_item.parent.mkdir(parents=True, exist_ok=True)
                    atomic_op.track_created_dir(target_item.parent)
                    if target_item.exists():
                        target_item.unlink()
                    target_item.hardlink_to(item)
                    atomic_op.track_created_file(target_item)
        return True

    def _execute_softlink(self, source: Path, target: Path, atomic_op: AtomicFileOperation) -> bool:
        """执行软链接操作"""
        target.parent.mkdir(parents=True, exist_ok=True)
        atomic_op.track_created_dir(target.parent)
        if target.exists():
            if target.is_symlink():
                target.unlink()
            elif target.is_dir():
                shutil.rmtree(target)
            else:
                target.unlink()
        target.symlink_to(source)
        atomic_op.track_created_file(target)
        return True

    def _update_progress(self, task_id: str, current: int, total: int, status: str = None):
        """更新同步进度"""
        if not self._enable_progress:
            return
            
        with self._progress_lock:
            if task_id not in self._sync_progress:
                self._sync_progress[task_id] = {
                    'current': 0,
                    'total': 0,
                    'percentage': 0.0,
                    'status': SyncStatus.IDLE.value,
                    'start_time': datetime.now(),
                    'speed': 0.0
                }
                
            progress = self._sync_progress[task_id]
            progress['current'] = current
            progress['total'] = total
            progress['percentage'] = (current / total * 100) if total > 0 else 0
            
            if status:
                progress['status'] = status
                
            # 计算速度
            elapsed = (datetime.now() - progress['start_time']).total_seconds()
            if elapsed > 0:
                progress['speed'] = current / elapsed
                
            logger.debug(f"同步进度更新 {task_id}: {current}/{total} ({progress['percentage']:.1f}%)")

    def _get_filtered_files(self, path: Path, max_depth: int = -1, current_depth: int = 0) -> List[Path]:
        """获取过滤后的文件列表"""
        files = []
        
        try:
            if max_depth != -1 and current_depth > max_depth:
                return files
                
            for item in path.iterdir():
                if item.is_file():
                    if self._should_sync_file(item):
                        files.append(item)
                elif item.is_dir():
                    # 递归获取子目录文件
                    files.extend(self._get_filtered_files(item, max_depth, current_depth + 1))
                    
        except Exception as e:
            logger.error(f"获取文件列表失败 {path}: {str(e)}")
            
        return files

    def _send_notification(self, title: str, text: str, image: str = None):
        """
        发送通知消息
        """
        if not self._enable_notifications:
            return
        
        try:
            # 获取可用的通知服务
            notification_services = self._notification_helper.get_services()
            if not notification_services:
                logger.warning("未找到可用的通知服务")
                return
            
            # 如果指定了特定渠道，则只发送到这些渠道
            if self._notification_channels:
                for channel_name in self._notification_channels:
                    service = notification_services.get(channel_name)
                    if service and service.instance:
                        try:
                            service.instance.post_message(
                                title=title,
                                text=text,
                                image=image
                            )
                            logger.info(f"通知已发送到 {channel_name}")
                        except Exception as e:
                            logger.error(f"发送通知到 {channel_name} 失败: {str(e)}")
            else:
                # 发送到所有可用渠道
                for service_info in notification_services.values():
                    if service_info.instance:
                        try:
                            service_info.instance.post_message(
                                title=title,
                                text=text,
                                image=image
                            )
                            logger.info(f"通知已发送到 {service_info.name}")
                        except Exception as e:
                            logger.error(f"发送通知到 {service_info.name} 失败: {str(e)}")
                            
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}")

    @cached(region="transfersync", ttl=300)
    def _get_media_library_paths(self) -> List[Path]:
        """
        获取媒体库路径（带缓存）
        """
        paths = []
        try:
            # 从媒体服务器配置中获取库路径
            mediaserver_services = self._mediaserver_helper.get_services()
            for service_info in mediaserver_services.values():
                if service_info.config and hasattr(service_info.config, 'library_paths'):
                    if service_info.config.library_paths:
                        for path_str in service_info.config.library_paths:
                            path = Path(path_str)
                            if path.exists() and path not in paths:
                                paths.append(path)
            
            # 从系统配置中获取媒体库路径
            if hasattr(settings, 'LIBRARY_PATH') and settings.LIBRARY_PATH:
                path = Path(settings.LIBRARY_PATH)
                if path.exists() and path not in paths:
                    paths.append(path)
                    
        except Exception as e:
            logger.error(f"获取媒体路径失败: {str(e)}")
        
        return paths

    def sync_single_action(self, action_content) -> Tuple[bool, Any]:
        """
        工作流动作：同步单个文件/目录
        """
        try:
            # 从动作内容中获取路径信息
            if hasattr(action_content, 'path') and action_content.path:
                self._sync_single_item(str(action_content.path))
                
                # 发送成功通知
                self._send_notification(
                    title="文件同步完成",
                    text=f"已成功同步: {action_content.path}"
                )
                
                return True, action_content
            else:
                logger.warning("工作流动作中未找到有效的路径信息")
                return False, action_content
                
        except Exception as e:
            logger.error(f"工作流同步单个项目失败: {str(e)}")
            return False, action_content

    def sync_incremental_action(self, action_content) -> Tuple[bool, Any]:
        """
        工作流动作：执行增量同步
        """
        try:
            self._incremental_sync()
            return True, action_content
        except Exception as e:
            logger.error(f"工作流增量同步失败: {str(e)}")
            return False, action_content

    def sync_full_action(self, action_content) -> Tuple[bool, Any]:
        """
        工作流动作：执行全量同步
        """
        try:
            self._full_sync()
            return True, action_content
        except Exception as e:
            logger.error(f"工作流全量同步失败: {str(e)}")
            return False, action_content

    def _incremental_sync(self):
        """
        增量同步
        """
        if not self._enabled or not self._copy_paths:
            return
        
        logger.info("开始执行增量同步")
        try:
            # 获取媒体库路径进行扫描
            media_paths = self._get_media_library_paths()
            if not media_paths:
                logger.warning("未找到可同步的媒体路径")
                return
            
            # 上次同步时间
            cutoff_time = self._last_sync_time or (datetime.now() - timedelta(hours=6))
            
            synced_count = 0
            for media_path in media_paths:
                synced_count += self._sync_path_incremental(media_path, cutoff_time)
            
            self._last_sync_time = datetime.now()
            message = f"增量同步完成，共同步 {synced_count} 个项目"
            logger.info(message)
            
            # 发送通知
            self._send_notification(
                title="增量同步完成",
                text=message
            )
            
        except Exception as e:
            error_msg = f"增量同步失败: {str(e)}"
            logger.error(error_msg)
            # 发送错误通知
            self._send_notification(
                title="增量同步失败",
                text=error_msg
            )

    def _full_sync(self):
        """
        全量同步
        """
        if not self._enabled or not self._copy_paths:
            return
        
        logger.info("开始执行全量同步")
        try:
            # 获取媒体库路径进行扫描
            media_paths = self._get_media_library_paths()
            if not media_paths:
                logger.warning("未找到可同步的媒体路径")
                return
            
            synced_count = 0
            for media_path in media_paths:
                synced_count += self._sync_path_full(media_path)
            
            self._last_sync_time = datetime.now()
            message = f"全量同步完成，共同步 {synced_count} 个项目"
            logger.info(message)
            
            # 发送通知
            self._send_notification(
                title="全量同步完成",
                text=message
            )
            
        except Exception as e:
            error_msg = f"全量同步失败: {str(e)}"
            logger.error(error_msg)
            # 发送错误通知
            self._send_notification(
                title="全量同步失败",
                text=error_msg
            )

    def _sync_path_incremental(self, media_path: Path, cutoff_time: datetime) -> int:
        """
        增量同步指定路径
        """
        synced_count = 0
        try:
            if not media_path.exists():
                return synced_count
            
            for item in media_path.iterdir():
                if item.is_file():
                    # 检查文件修改时间
                    mtime = datetime.fromtimestamp(item.stat().st_mtime)
                    if mtime > cutoff_time:
                        self._sync_single_item(str(item))
                        synced_count += 1
                elif item.is_dir():
                    # 递归处理子目录
                    synced_count += self._sync_path_incremental(item, cutoff_time)
                    
        except Exception as e:
            logger.error(f"增量同步路径 {media_path} 失败: {str(e)}")
        
        return synced_count

    def _sync_path_full(self, media_path: Path) -> int:
        """
        全量同步指定路径
        """
        synced_count = 0
        try:
            if not media_path.exists():
                return synced_count
            
            for item in media_path.iterdir():
                self._sync_single_item(str(item))
                synced_count += 1
                    
        except Exception as e:
            logger.error(f"全量同步路径 {media_path} 失败: {str(e)}")
        
        return synced_count

    @cached(region="transfersync", ttl=600)
    def _get_sync_statistics(self) -> Dict[str, Any]:
        """
        获取同步统计信息（带缓存）
        """
        with self._lock:
            total_files = 0
            total_size = 0
            recent_syncs = []
            
            for path, record in self._sync_records.items():
                total_files += 1
                try:
                    file_path = Path(path)
                    if file_path.exists():
                        if file_path.is_file():
                            total_size += file_path.stat().st_size
                        else:
                            # 对于目录，计算总大小
                            for item in file_path.rglob('*'):
                                if item.is_file():
                                    total_size += item.stat().st_size
                    
                    # 记录最近的同步
                    if len(recent_syncs) < 10:
                        recent_syncs.append({
                            'path': path,
                            'sync_time': record['sync_time'].strftime('%Y-%m-%d %H:%M:%S'),
                            'targets': record['target_paths']
                        })
                except Exception as e:
                    logger.debug(f"统计文件 {path} 大小失败: {str(e)}")
            
            return {
                'total_files': total_files,
                'total_size': total_size,
                'recent_syncs': sorted(recent_syncs, key=lambda x: x['sync_time'], reverse=True)
            }

    def _get_sync_status(self) -> Dict[str, Any]:
        """
        获取同步状态信息
        """
        try:
            stats = self._get_sync_statistics()
            last_sync = self._last_sync_time.strftime("%Y-%m-%d %H:%M:%S") if self._last_sync_time else "未执行"
            
            return {
                'total_synced': stats['total_files'],
                'total_size': self._format_size(stats['total_size']),
                'last_sync_time': last_sync,
                'enabled': self._enabled,
                'recent_syncs': stats['recent_syncs']
            }
        except Exception as e:
            logger.error(f"获取同步状态失败: {str(e)}")
            return {
                'total_synced': 0,
                'total_size': '0 B',
                'last_sync_time': '未知',
                'enabled': self._enabled,
                'recent_syncs': []
            }

    def _format_size(self, size_bytes: int) -> str:
        """
        格式化文件大小显示
        """
        if size_bytes == 0:
            return "0 B"
        size_units = ['B', 'KB', 'MB', 'GB', 'TB']
        i = 0
        while size_bytes >= 1024 and i < len(size_units) - 1:
            size_bytes /= 1024.0
            i += 1
        return f"{size_bytes:.2f} {size_units[i]}"

    def sync_now(self) -> dict:
        """
        API接口：立即同步
        """
        try:
            # 执行全量同步
            self._full_sync()
            return {
                "code": 0,
                "message": "同步任务已启动"
            }
        except Exception as e:
            logger.error(f"立即同步失败: {str(e)}")
            return {
                "code": 1,
                "message": f"同步失败: {str(e)}"
            }

    def clear_cache(self):
        """
        清理插件缓存
        """
        try:
            if self._cache:
                self._cache.clear()
                logger.info("插件缓存已清理")
        except Exception as e:
            logger.error(f"清理缓存失败: {str(e)}")

    def _process_sync_queue(self):
        """处理同步队列"""
        if not self._sync_queue or self._current_status == SyncStatus.RUNNING:
            return
            
        try:
            self._current_status = SyncStatus.RUNNING
            
            if self._executor:
                while self._sync_queue:
                    with self._lock:
                        if not self._sync_queue:
                            break
                        sync_item = self._sync_queue.pop(0)
                    
                    # 提交同步任务
                    future = self._executor.submit(
                        self._execute_sync_strategy, 
                        sync_item['source'], 
                        sync_item['target']
                    )
                    
                    try:
                        if future.result(timeout=30):  # 30秒超时
                            logger.debug(f"队列同步完成: {sync_item['source']}")
                        else:
                            logger.warning(f"队列同步失败: {sync_item['source']}")
                    except Exception as e:
                        logger.error(f"队列同步异常 {sync_item['source']}: {str(e)}")
                        
        except Exception as e:
            logger.error(f"处理同步队列失败: {str(e)}")
        finally:
            self._current_status = SyncStatus.IDLE

    def pause_sync(self):
        """暂停同步"""
        self._current_status = SyncStatus.PAUSED
        logger.info("同步已暂停")

    def resume_sync(self):
        """恢复同步"""
        if self._current_status == SyncStatus.PAUSED:
            self._current_status = SyncStatus.IDLE
            self._process_sync_queue()
            logger.info("同步已恢复")

    def get_sync_status(self) -> Dict[str, Any]:
        """获取当前同步状态"""
        return {
            'status': self._current_status.value,
            'queue_size': len(self._sync_queue),
            'active_tasks': len([p for p in self._sync_progress.values() 
                               if p['status'] not in [SyncStatus.COMPLETED.value, SyncStatus.ERROR.value]])
        }

    def stop_service(self):
        """
        退出插件
        """
        try:
            # 停止调度器
            if self._scheduler:
                self._scheduler.shutdown(wait=False)
                self._scheduler = None
                logger.info("同步调度器已停止")
            
            # 停止线程池
            if self._executor:
                self._executor.shutdown(wait=True, timeout=30)
                self._executor = None
                logger.info("线程池已停止")
            
            # 清理缓存
            self.clear_cache()
            
            # 清理资源
            self._sync_records.clear()
            self._sync_queue.clear()
            self._sync_progress.clear()
            self._current_status = SyncStatus.IDLE
            
        except Exception as e:
            logger.error(f"停止服务失败: {str(e)}")

    # 多事件监听机制
    def _register_event_listeners(self):
        """注册所选的事件监听器"""
        # 注册选定的事件监听器
        for event_type in self._trigger_events:
            self._register_event_listener(event_type)
            
        logger.info(f"已注册监听事件: {[e.value for e in self._trigger_events]}")
    
    def _register_event_listener(self, event_type: TriggerEvent):
        """为指定事件类型注册监听器"""
        handler_map = {
            TriggerEvent.TRANSFER_COMPLETE: self._on_transfer_complete,
            TriggerEvent.DOWNLOAD_ADDED: self._on_download_added,
            TriggerEvent.SUBSCRIBE_COMPLETE: self._on_subscribe_complete,
            TriggerEvent.MEDIA_ADDED: self._on_media_added,
            TriggerEvent.FILE_MOVED: self._on_file_moved,
            TriggerEvent.DIRECTORY_SCAN_COMPLETE: self._on_directory_scan_complete,
            TriggerEvent.SCRAPE_COMPLETE: self._on_scrape_complete,
            TriggerEvent.PLUGIN_TRIGGERED: self._on_plugin_triggered
        }
        
        handler = handler_map.get(event_type)
        if handler:
            # 使用装饰器注册事件处理器
            # 注意：这里需要根据MoviePilot V2的实际事件系统API来调整
            eventmanager.register(event_type.value, handler)
            logger.debug(f"已注册事件处理器: {event_type.value}")

    @event_handler(TriggerEvent.TRANSFER_COMPLETE)
    def _on_transfer_complete(self, event: Event):
        """处理整理完成事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取整理后的路径信息
        src_path = event_data.get('src_path')
        dest_path = event_data.get('dest_path')
        
        if dest_path and Path(dest_path).exists():
            # 执行同步
            self._sync_file_or_directory(dest_path, f"整理完成事件触发同步")
            return True
        else:
            logger.warning(f"整理完成事件缺少有效路径信息: {event_data}")
            return False

    @event_handler(TriggerEvent.DOWNLOAD_ADDED)
    def _on_download_added(self, event: Event):
        """处理下载添加事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取下载文件路径
        file_path = event_data.get('file_path') or event_data.get('path')
        
        if file_path and Path(file_path).exists():
            self._sync_file_or_directory(file_path, f"下载添加事件触发同步")
            return True
        else:
            logger.warning(f"下载添加事件缺少有效路径信息: {event_data}")
            return False

    @event_handler(TriggerEvent.SUBSCRIBE_COMPLETE)
    def _on_subscribe_complete(self, event: Event):
        """处理订阅完成事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取订阅完成的媒体路径
        media_path = event_data.get('media_path') or event_data.get('path')
        
        if media_path and Path(media_path).exists():
            self._sync_file_or_directory(media_path, f"订阅完成事件触发同步")
            return True
        else:
            logger.warning(f"订阅完成事件缺少有效路径信息: {event_data}")
            return False

    @event_handler(TriggerEvent.MEDIA_ADDED)
    def _on_media_added(self, event: Event):
        """处理媒体添加事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取媒体文件路径
        media_path = event_data.get('file_path') or event_data.get('path')
        
        if media_path and Path(media_path).exists():
            self._sync_file_or_directory(media_path, f"媒体添加事件触发同步")
            return True
        else:
            logger.warning(f"媒体添加事件缺少有效路径信息: {event_data}")
            return False

    @event_handler(TriggerEvent.FILE_MOVED)
    def _on_file_moved(self, event: Event):
        """处理文件移动事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取移动后的文件路径
        dest_path = event_data.get('dest_path') or event_data.get('new_path')
        
        if dest_path and Path(dest_path).exists():
            self._sync_file_or_directory(dest_path, f"文件移动事件触发同步")
            return True
        else:
            logger.warning(f"文件移动事件缺少有效路径信息: {event_data}")
            return False

    @event_handler(TriggerEvent.DIRECTORY_SCAN_COMPLETE)
    def _on_directory_scan_complete(self, event: Event):
        """处理目录扫描完成事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取扫描的目录路径
        scan_path = event_data.get('scan_path') or event_data.get('path')
        
        if scan_path and Path(scan_path).exists():
            self._sync_file_or_directory(scan_path, f"目录扫描完成事件触发同步")
            return True
        else:
            logger.warning(f"目录扫描完成事件缺少有效路径信息: {event_data}")
            return False

    @event_handler(TriggerEvent.SCRAPE_COMPLETE)
    def _on_scrape_complete(self, event: Event):
        """处理刮削完成事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取刮削完成的媒体路径
        media_path = event_data.get('media_path') or event_data.get('path')
        
        if media_path and Path(media_path).exists():
            self._sync_file_or_directory(media_path, f"刮削完成事件触发同步")
            return True
        else:
            logger.warning(f"刮削完成事件缺少有效路径信息: {event_data}")
            return False

    @event_handler(TriggerEvent.PLUGIN_TRIGGERED)
    def _on_plugin_triggered(self, event: Event):
        """处理插件触发事件"""
        event_data = event.event_data if hasattr(event, 'event_data') else {}
        
        # 获取触发路径
        trigger_path = event_data.get('path') or event_data.get('file_path')
        
        if trigger_path and Path(trigger_path).exists():
            self._sync_file_or_directory(trigger_path, f"插件触发事件同步")
            return True
        else:
            logger.warning(f"插件触发事件缺少有效路径信息: {event_data}")
            return False

    def _should_handle_event(self, event: Event, event_type: TriggerEvent) -> bool:
        """检查是否应该处理该事件"""
        if not self._enabled:
            return False
            
        # 检查事件过滤条件
        if self._event_conditions and not self._check_event_conditions(event, event_type):
            logger.debug(f"事件不满足过滤条件，跳过处理: {event_type.value}")
            return False
            
        return True

    def _check_event_conditions(self, event: Event, event_type: TriggerEvent) -> bool:
        """检查事件是否满足过滤条件"""
        if not self._event_conditions:
            return True
            
        try:
            event_data = event.event_data if hasattr(event, 'event_data') else {}
            
            for condition_key, condition_value in self._event_conditions.items():
                event_value = event_data.get(condition_key)
                
                # 处理不同类型的条件匹配
                if not self._match_condition(event_value, condition_value):
                    return False
                    
            return True
            
        except Exception as e:
            logger.error(f"检查事件条件失败: {str(e)}")
            return True  # 出错时默认处理事件

    def _match_condition(self, event_value: Any, condition_value: str) -> bool:
        """匹配单个条件"""
        if event_value is None:
            return False
            
        try:
            # 处理数值比较条件
            if condition_value.startswith('>='):
                return float(event_value) >= float(condition_value[2:])
            elif condition_value.startswith('<='):
                return float(event_value) <= float(condition_value[2:])
            elif condition_value.startswith('>'):
                return float(event_value) > float(condition_value[1:])
            elif condition_value.startswith('<'):
                return float(event_value) < float(condition_value[1:])
            elif condition_value.startswith('!='):
                return str(event_value) != condition_value[2:]
            else:
                # 字符串完全匹配或包含匹配
                return str(condition_value).lower() in str(event_value).lower()
                
        except (ValueError, TypeError):
            # 无法进行数值比较时，进行字符串匹配
            return str(condition_value).lower() in str(event_value).lower()

    def _update_event_stats(self, event_type: TriggerEvent, success: bool, 
                           processing_time: float = 0.0, error_type: str = None):
        """更新事件统计信息"""
        if not hasattr(self, '_event_statistics'):
            self._event_statistics = {}
        if not hasattr(self, '_stats_lock'):
            self._stats_lock = threading.RLock()
            
        event_key = event_type.value
        
        with self._stats_lock:
            if event_key not in self._event_statistics:
                self._event_statistics[event_key] = {
                    'total_count': 0,
                    'success_count': 0,
                    'failure_count': 0,
                    'last_triggered': None,
                    'last_success': None,
                    'last_failure': None,
                    'avg_processing_time': 0.0,
                    'total_processing_time': 0.0,
                    'error_types': {},  # 错误类型统计
                    'performance_history': []  # 最近10次处理时间
                }
            
            stats = self._event_statistics[event_key]
            stats['total_count'] += 1
            current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            stats['last_triggered'] = current_time
            
            # 更新处理时间统计
            if processing_time > 0:
                stats['total_processing_time'] += processing_time
                stats['avg_processing_time'] = stats['total_processing_time'] / stats['total_count']
                
                # 保持最近10次处理时间历史
                stats['performance_history'].append({
                    'time': current_time,
                    'duration': processing_time,
                    'success': success
                })
                if len(stats['performance_history']) > 10:
                    stats['performance_history'].pop(0)
            
            if success:
                stats['success_count'] += 1
                stats['last_success'] = current_time
            else:
                stats['failure_count'] += 1
                stats['last_failure'] = current_time
                
                # 统计错误类型
                if error_type:
                    if error_type not in stats['error_types']:
                        stats['error_types'][error_type] = 0
                    stats['error_types'][error_type] += 1
            
            # 添加性能告警检测
            self._check_performance_alerts(event_type, processing_time, success)
        
        status = "成功" if success else "失败"
        if error_type:
            logger.debug(f"事件处理统计 - {event_type.value}: {status} ({error_type}), 耗时: {processing_time:.2f}s")
        else:
            logger.debug(f"事件处理统计 - {event_type.value}: {status}, 耗时: {processing_time:.2f}s")

    def _check_performance_alerts(self, event_type: TriggerEvent, processing_time: float, success: bool):
        """检查性能告警"""
        try:
            # 处理时间过长告警（超过30秒）
            if processing_time > 30.0:
                logger.warning(f"事件处理耗时过长: {event_type.value}, 耗时: {processing_time:.2f}s")
                if self._enable_notifications:
                    self._send_notification("性能告警", 
                                          f"事件处理耗时过长: {self._get_event_display_name(event_type.value)}, "
                                          f"耗时: {processing_time:.2f}s")
            
            # 连续失败告警
            if not success and hasattr(self, '_event_statistics'):
                stats = self._event_statistics.get(event_type.value, {})
                recent_failures = 0
                
                # 检查最近5次处理是否都失败
                for record in stats.get('performance_history', [])[-5:]:
                    if not record['success']:
                        recent_failures += 1
                
                if recent_failures >= 3:
                    logger.warning(f"事件处理连续失败: {event_type.value}, 最近{recent_failures}次失败")
                    if self._enable_notifications:
                        self._send_notification("事件处理告警", 
                                              f"事件连续失败: {self._get_event_display_name(event_type.value)}, "
                                              f"最近{recent_failures}次处理失败")
                        
        except Exception as e:
            logger.error(f"性能告警检查失败: {str(e)}")

    def get_event_statistics(self) -> Dict[str, Any]:
        """获取事件统计信息"""
        if not hasattr(self, '_event_statistics'):
            self._event_statistics = {}
            
        # 添加成功率计算
        for event_key, stats in self._event_statistics.items():
            if stats['total_count'] > 0:
                stats['success_rate'] = round((stats['success_count'] / stats['total_count']) * 100, 2)
            else:
                stats['success_rate'] = 0
        
        return self._event_statistics

    def reset_event_statistics(self):
        """重置事件统计信息"""
        self._event_statistics = {}
        logger.info("事件统计信息已重置")