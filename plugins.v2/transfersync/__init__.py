"""
整理后同步插件 - 重构版本
"""
import time
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from app.core.config import settings
from app.core.event import Event, EventType, eventmanager
from app.core.cache import cached, TTLCache
from app.plugins import _PluginBase
from app.log import logger
from app.schemas.types import NotificationType
from app.schemas import NotificationConf
from app.helper.notification import NotificationHelper
from app.helper.mediaserver import MediaServerHelper
from app.helper.storage import StorageHelper

# 导入模块
from .sync_types import SyncStrategy, SyncMode, FileFilterType, SyncStatus, TriggerEvent, EventCondition
from .exceptions import SyncException, SyncPermissionError, SyncSpaceError, SyncNetworkError
from .file_operations import AtomicFileOperation
from .config_validator import ConfigValidator
from .event_handler import event_handler
from .sync_operations import SyncOperations

# 导入核心功能模块
from .command_handler import CommandHandler


class TransferSync(_PluginBase):
    # 插件名称
    plugin_name = "整理后同步"
    # 插件描述
    plugin_desc = "监听可选择的多种事件类型，根据过滤条件自动同步文件到指定位置，支持多种同步策略、事件统计监控、增量和全量同步。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/sync.png"
    # 插件版本
    plugin_version = "1.4"
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
    _sync_root_path = ""  # 简化为单一根路径
    _sync_target_path = ""  # 同步目标路径
    _delay_minutes = 5  # 延迟执行时间（分钟）
    _enable_immediate_execution = True  # 启用立即执行功能
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
    _last_sync_time = None
    _last_event_time = {}  # 记录每个路径的最后事件时间
    _pending_syncs = {}  # 待执行的同步任务
    _validator = None
    _sync_ops = None
    _lock = threading.Lock()
    _local_cache = TTLCache(maxsize=100, ttl=300)  # 5分钟TTL本地缓存
    _performance_metrics = {}  # 性能监控指标
    _health_status = {"status": "unknown", "checks": {}}  # 健康检查状态

    def init_plugin(self, config: dict = None):
        """
        初始化插件
        """
        if config:
            self._enabled = config.get("enabled", False)

            # 基础配置
            self._sync_root_path = config.get("sync_root_path", "")
            self._sync_target_path = config.get("sync_target_path", "")
            self._delay_minutes = config.get("delay_minutes", 5)
            self._enable_immediate_execution = config.get("enable_immediate_execution", True)
            self._enable_incremental = config.get("enable_incremental", False)
            self._incremental_cron = config.get("incremental_cron", "0 */6 * * *")
            self._enable_full_sync = config.get("enable_full_sync", False)
            self._full_sync_cron = config.get("full_sync_cron", "0 2 * * 0")
            self._enable_notifications = config.get("enable_notifications", False)
            self._notification_channels = self._parse_list(config.get("notification_channels", ""))

            # 高级配置
            try:
                self._sync_strategy = SyncStrategy(config.get("sync_strategy", "copy"))
            except ValueError:
                self._sync_strategy = SyncStrategy.COPY
                
            try:
                self._sync_mode = SyncMode(config.get("sync_mode", "immediate"))
            except ValueError:
                self._sync_mode = SyncMode.IMMEDIATE
                
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
        self._storage_helper = StorageHelper()
        
        # 清理旧的缓存数据
        if self._local_cache:
            self._local_cache.clear()
        
        # 初始化配置验证器
        self._validator = ConfigValidator()
        
        # 初始化同步操作类
        self._sync_ops = SyncOperations(self)

        # 初始化核心模块
        self._command_handler = None

        # 注册事件监听器
        if self._enabled:
            self._register_event_listeners()
            # 设置定时任务
            logger.info("定时同步功能已启用")

        logger.info("TransferSync插件初始化完成")

    @property
    def command_handler(self):
        """延迟初始化命令处理器"""
        if self._command_handler is None:
            self._command_handler = CommandHandler(self)
        return self._command_handler

    def _parse_list(self, list_str: str, separator: str = ',') -> List[str]:
        """解析列表字符串"""
        if not list_str:
            return []
        return [item.strip() for item in list_str.split(separator) if item.strip()]

    def _parse_event_conditions(self, conditions_str: str) -> Dict[str, Any]:
        """解析事件过滤条件"""
        conditions = {}
        if not conditions_str:
            return conditions
        
        try:
            for line in conditions_str.split('\n'):
                line = line.strip()
                if '=' in line:
                    key, value = line.split('=', 1)
                    conditions[key.strip()] = value.strip()
        except Exception as e:
            logger.error(f"解析事件条件失败: {str(e)}")
        
        return conditions

    @cached(region="transfersync_config", ttl=300, skip_none=True)
    def _validate_config(self):
        """验证配置（带缓存）"""
        if self._validator:
            validation_result = self._validator.validate_all_config(self._get_config_dict())
            if not validation_result.get('valid', True):
                logger.warning(f"配置验证警告: {validation_result.get('warnings', [])}")
                if validation_result.get('errors'):
                    logger.error(f"配置验证错误: {validation_result.get('errors', [])}")
            return validation_result
        return {"valid": True}

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        """停止服务"""
        try:
            # 取消事件监听
            self._unregister_event_listeners()
            
            # 清理缓存
            self._clear_plugin_cache()
            
            logger.info("TransferSync服务已停止")
        except Exception as e:
            logger.error(f"停止TransferSync服务失败: {str(e)}")

    def _clear_plugin_cache(self):
        """清理插件缓存"""
        try:
            # 清理方法级缓存
            if hasattr(self._validate_config, 'cache_clear'):
                self._validate_config.cache_clear()
            if hasattr(self._get_directories, 'cache_clear'):
                self._get_directories.cache_clear()
            if hasattr(self._get_notification_options, 'cache_clear'):
                self._get_notification_options.cache_clear()
            
            # 清理本地TTL缓存
            if self._local_cache:
                self._local_cache.clear()
                
            logger.info("插件缓存已清理")
        except Exception as e:
            logger.error(f"清理插件缓存失败: {str(e)}")

    def _get_config_dict(self) -> Dict:
        """获取配置字典"""
        return {
            "enabled": self._enabled,
            "sync_root_path": self._sync_root_path,
            "sync_target_path": self._sync_target_path,
            "delay_minutes": self._delay_minutes,
            "enable_immediate_execution": self._enable_immediate_execution,
            "enable_incremental": self._enable_incremental,
            "incremental_cron": self._incremental_cron,
            "enable_full_sync": self._enable_full_sync,
            "full_sync_cron": self._full_sync_cron,
            "enable_notifications": self._enable_notifications,
            "notification_channels": ','.join(self._notification_channels),
            "sync_strategy": self._sync_strategy.value,
            "sync_mode": self._sync_mode.value,
            "max_depth": self._max_depth,
            "file_filters": ','.join(self._file_filters),
            "exclude_patterns": ','.join(self._exclude_patterns),
            "max_file_size": self._max_file_size,
            "min_file_size": self._min_file_size,
            "enable_progress": self._enable_progress,
            "max_workers": self._max_workers,
            "batch_size": self._batch_size,
            "trigger_events": [event.value for event in self._trigger_events]
        }

    def _register_event_listeners(self):
        """注册事件监听器"""
        try:
            for trigger_event in self._trigger_events:
                if trigger_event == TriggerEvent.TRANSFER_COMPLETE:
                    eventmanager.register(EventType.TransferComplete, self._on_transfer_complete)
                elif trigger_event == TriggerEvent.DOWNLOAD_ADDED:
                    eventmanager.register(EventType.DownloadAdded, self._on_download_added)
                elif trigger_event == TriggerEvent.SUBSCRIBE_COMPLETE:
                    eventmanager.register(EventType.SubscribeComplete, self._on_subscribe_complete)
                elif trigger_event == TriggerEvent.MEDIA_ADDED:
                    eventmanager.register(EventType.MediaAdded, self._on_media_added)
                elif trigger_event == TriggerEvent.FILE_MOVED:
                    eventmanager.register(EventType.FileMoved, self._on_file_moved)
                elif trigger_event == TriggerEvent.DIRECTORY_SCAN_COMPLETE:
                    eventmanager.register(EventType.DirectoryScanComplete, self._on_directory_scan_complete)
                elif trigger_event == TriggerEvent.SCRAPE_COMPLETE:
                    eventmanager.register(EventType.ScrapeComplete, self._on_scrape_complete)
                elif trigger_event == TriggerEvent.PLUGIN_TRIGGERED:
                    eventmanager.register(EventType.PluginAction, self._on_plugin_triggered)
                    
            logger.info(f"已注册 {len(self._trigger_events)} 个事件监听器")
        except Exception as e:
            logger.error(f"注册事件监听器失败: {str(e)}")

    def _unregister_event_listeners(self):
        """取消事件监听器注册"""
        try:
            event_mappings = {
                TriggerEvent.TRANSFER_COMPLETE: (EventType.TransferComplete, self._on_transfer_complete),
                TriggerEvent.DOWNLOAD_ADDED: (EventType.DownloadAdded, self._on_download_added),
                TriggerEvent.SUBSCRIBE_COMPLETE: (EventType.SubscribeComplete, self._on_subscribe_complete),
                TriggerEvent.MEDIA_ADDED: (EventType.MediaAdded, self._on_media_added),
                TriggerEvent.FILE_MOVED: (EventType.FileMoved, self._on_file_moved),
                TriggerEvent.DIRECTORY_SCAN_COMPLETE: (EventType.DirectoryScanComplete, self._on_directory_scan_complete),
                TriggerEvent.SCRAPE_COMPLETE: (EventType.ScrapeComplete, self._on_scrape_complete),
                TriggerEvent.PLUGIN_TRIGGERED: (EventType.PluginAction, self._on_plugin_triggered)
            }
            
            for trigger_event in self._trigger_events:
                if trigger_event in event_mappings:
                    event_type, handler = event_mappings[trigger_event]
                    eventmanager.unregister(event_type, handler)
                    
            logger.info("已取消所有事件监听器注册")
        except Exception as e:
            logger.error(f"取消事件监听器注册失败: {str(e)}")

    # 事件处理方法
    def _on_transfer_complete(self, event: Event):
        """整理完成事件处理"""
        self._handle_event(event, TriggerEvent.TRANSFER_COMPLETE)

    def _on_download_added(self, event: Event):
        """下载添加事件处理"""
        self._handle_event(event, TriggerEvent.DOWNLOAD_ADDED)

    def _on_subscribe_complete(self, event: Event):
        """订阅完成事件处理"""
        self._handle_event(event, TriggerEvent.SUBSCRIBE_COMPLETE)

    def _on_media_added(self, event: Event):
        """媒体添加事件处理"""
        self._handle_event(event, TriggerEvent.MEDIA_ADDED)

    def _on_file_moved(self, event: Event):
        """文件移动事件处理"""
        self._handle_event(event, TriggerEvent.FILE_MOVED)

    def _on_directory_scan_complete(self, event: Event):
        """目录扫描完成事件处理"""
        self._handle_event(event, TriggerEvent.DIRECTORY_SCAN_COMPLETE)

    def _on_scrape_complete(self, event: Event):
        """刮削完成事件处理"""
        self._handle_event(event, TriggerEvent.SCRAPE_COMPLETE)

    def _on_plugin_triggered(self, event: Event):
        """插件触发事件处理"""
        # 检查是否为命令事件
        if event.event_data and 'action' in event.event_data:
            action = event.event_data.get('action')
            # 使用命令处理器处理命令
            result = self.command_handler.handle_command(action, **event.event_data)
            
            # 发送响应消息
            if event.event_data.get('channel'):
                response_text = self.command_handler.format_command_response(result)
                self._send_notification("TransferSync 命令响应", response_text)
        else:
            # 其他插件触发事件按原逻辑处理
            self._handle_event(event, TriggerEvent.PLUGIN_TRIGGERED)

    def _handle_event(self, event: Event, event_type: TriggerEvent):
        """统一事件处理方法"""
        if not self._should_handle_event(event, event_type):
            return

        try:
            start_time = datetime.now()
            sync_path = self._extract_sync_path(event.event_data, event_type)
            
            if sync_path:
                # 记录事件时间
                self._last_event_time[sync_path] = start_time
                
                if self._enable_immediate_execution:
                    # 立即执行
                    logger.info(f"立即处理{self._get_event_display_name(event_type.value)}事件，同步路径: {sync_path}")
                    result = self._sync_ops.sync_directory(sync_path)
                    success = result.get('success', False)
                    error_type = result.get('error_type') if not success else None
                else:
                    # 延迟执行
                    logger.info(f"延迟{self._delay_minutes}分钟处理{self._get_event_display_name(event_type.value)}事件，同步路径: {sync_path}")
                    self._schedule_delayed_sync(sync_path, event_type)
                    success = True
                    error_type = None
            else:
                logger.warning(f"{self._get_event_display_name(event_type.value)}事件无有效同步路径")
                success = False
                error_type = "no_sync_path"

            processing_time = (datetime.now() - start_time).total_seconds()
            self._update_event_stats(event_type, success, processing_time, error_type)

        except Exception as e:
            logger.error(f"处理{self._get_event_display_name(event_type.value)}事件失败: {str(e)}")
            processing_time = (datetime.now() - start_time).total_seconds()
            self._update_event_stats(event_type, False, processing_time, "exception")

    def _schedule_delayed_sync(self, sync_path: str, event_type: TriggerEvent):
        """安排延迟同步"""
        from threading import Timer
        
        task_id = f"{sync_path}_{int(time.time())}"
        
        def delayed_sync():
            try:
                # 检查是否在延迟期间有新的事件
                last_event = self._last_event_time.get(sync_path)
                if last_event and (datetime.now() - last_event).total_seconds() >= self._delay_minutes * 60:
                    logger.info(f"执行延迟同步任务: {sync_path}")
                    result = self._sync_ops.sync_directory(sync_path)
                    if result.get('success'):
                        logger.info(f"延迟同步完成: {sync_path}")
                    else:
                        logger.error(f"延迟同步失败: {sync_path}, {result.get('message')}")
                else:
                    logger.info(f"延迟期间有新事件，跳过同步: {sync_path}")
                
                # 清理任务记录
                with self._lock:
                    self._pending_syncs.pop(task_id, None)
                    
            except Exception as e:
                logger.error(f"延迟同步任务执行失败: {str(e)}")
        
        # 创建定时器
        timer = Timer(self._delay_minutes * 60, delayed_sync)
        timer.start()
        
        # 记录待执行任务
        with self._lock:
            self._pending_syncs[task_id] = {
                'path': sync_path,
                'event_type': event_type.value,
                'scheduled_time': datetime.now(),
                'timer': timer
            }

    def execute_immediate_sync(self, sync_path: str = None) -> dict:
        """立即执行同步"""
        try:
            if sync_path:
                # 同步指定路径
                result = self._sync_ops.sync_directory(sync_path)
            else:
                # 同步根路径
                if not self._sync_root_path:
                    return {"success": False, "message": "未配置同步根路径"}
                result = self._sync_ops.sync_directory(self._sync_root_path)
            
            if result.get('success'):
                self._last_sync_time = datetime.now()
                
            return result
            
        except Exception as e:
            logger.error(f"立即同步失败: {str(e)}")
            return {"success": False, "message": f"立即同步失败: {str(e)}"}

    def _extract_sync_path(self, event_data: Dict, event_type: TriggerEvent) -> Optional[str]:
        """从事件数据中提取同步路径"""
        if not event_data:
            return None
            
        # 根据不同事件类型提取路径
        path_keys = ['path', 'dir_path', 'dest_path', 'file_path', 'target_path']
        for key in path_keys:
            if key in event_data:
                return str(event_data[key])
        
        return None

    def _should_handle_event(self, event: Event, event_type: TriggerEvent) -> bool:
        """判断是否应该处理此事件"""
        if not self._enabled:
            return False
            
        if event_type not in self._trigger_events:
            return False
            
        # 检查事件过滤条件
        if self._event_conditions:
            for condition_key, condition_value in self._event_conditions.items():
                event_value = event.event_data.get(condition_key)
                if event_value != condition_value:
                    logger.debug(f"事件过滤条件不匹配: {condition_key}={event_value}, 期望={condition_value}")
                    return False
        
        return True

    def _get_event_display_name(self, event_value: str) -> str:
        """获取事件显示名称"""
        event_names = {
            "transfer_complete": "整理完成",
            "download_added": "下载添加", 
            "subscribe_complete": "订阅完成",
            "media_added": "媒体添加",
            "file_moved": "文件移动",
            "directory_scan_complete": "目录扫描完成",
            "scrape_complete": "刮削完成",
            "plugin_triggered": "插件触发"
        }
        return event_names.get(event_value, event_value)

    def _is_valid_event(self, event_value: str) -> bool:
        """验证事件值是否有效"""
        try:
            TriggerEvent(event_value)
            return True
        except ValueError:
            return False

    def _update_event_stats(self, event_type: TriggerEvent, success: bool, processing_time: float, error_type: str = None):
        """更新事件统计"""
        event_key = event_type.value
        if event_key not in self._event_statistics:
            self._event_statistics[event_key] = {
                'total_count': 0,
                'success_count': 0,
                'failed_count': 0,
                'avg_processing_time': 0.0,
                'last_success_time': None,
                'last_failed_time': None,
                'error_counts': {},
                'recent_errors': []
            }
        
        stats = self._event_statistics[event_key]
        stats['total_count'] += 1
        
        if success:
            stats['success_count'] += 1
            stats['last_success_time'] = datetime.now().isoformat()
        else:
            stats['failed_count'] += 1
            stats['last_failed_time'] = datetime.now().isoformat()
            
            if error_type:
                stats['error_counts'][error_type] = stats['error_counts'].get(error_type, 0) + 1
                stats['recent_errors'].append({
                    'error_type': error_type,
                    'timestamp': datetime.now().isoformat()
                })
                # 只保留最近20个错误
                stats['recent_errors'] = stats['recent_errors'][-20:]
        
        # 更新平均处理时间
        total_time = stats['avg_processing_time'] * (stats['total_count'] - 1) + processing_time
        stats['avg_processing_time'] = total_time / stats['total_count']

    # V2插件必需的抽象方法实现
    def get_api(self) -> List[Dict[str, Any]]:
        """获取API端点"""
        return [{
            "path": "/execute_immediate_sync",
            "endpoint": self.execute_immediate_sync,
            "methods": ["POST"],
            "summary": "立即执行同步"
        }]

    def get_command(self) -> List[Dict[str, Any]]:
        """获取插件命令"""
        return self.command_handler.get_command()

    def get_service(self) -> List[Dict[str, Any]]:
        """获取服务"""
        return [{
            "id": "transfersync",
            "name": "TransferSync同步服务",
            "trigger": "plugin"
        }]

    def get_page(self) -> List[Dict[str, Any]]:
        """获取插件页面"""
        # 获取统计信息
        stats_summary = self._get_stats_summary()
        pending_count = len(self._pending_syncs)
        
        return [
            {
                "component": "VContainer",
                "props": {
                    "fluid": True
                },
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "div",
                                        "text": "TransferSync - 整理后同步插件",
                                        "props": {
                                            "class": "text-h4 text-center mb-4"
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 6
                                },
                                "content": [
                                    {
                                        "component": "VCard",
                                        "props": {
                                            "variant": "outlined",
                                            "color": "primary" if self._enabled else "grey"
                                        },
                                        "content": [
                                            {
                                                "component": "VCardTitle",
                                                "props": {
                                                    "class": "d-flex align-center"
                                                },
                                                "content": [
                                                    {
                                                        "component": "VIcon",
                                                        "props": {
                                                            "icon": "mdi-sync" if self._enabled else "mdi-sync-off",
                                                            "class": "mr-2"
                                                        }
                                                    },
                                                    {
                                                        "component": "span",
                                                        "text": "运行状态"
                                                    }
                                                ]
                                            },
                                            {
                                                "component": "VCardText",
                                                "content": [
                                                    {
                                                        "component": "VChip",
                                                        "props": {
                                                            "color": "success" if self._enabled else "error",
                                                            "variant": "flat",
                                                            "size": "large"
                                                        },
                                                        "text": "运行中" if self._enabled else "已停止"
                                                    },
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "mt-3"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "div",
                                                                "text": f"同步策略: {self._sync_strategy.value}"
                                                            },
                                                            {
                                                                "component": "div",
                                                                "text": f"监听事件: {len(self._trigger_events)} 个"
                                                            },
                                                            {
                                                                "component": "div",
                                                                "text": f"待处理任务: {pending_count} 个"
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
                                "component": "VCol",
                                "props": {
                                    "cols": 12,
                                    "md": 6
                                },
                                "content": [
                                    {
                                        "component": "VCard",
                                        "props": {
                                            "variant": "outlined"
                                        },
                                        "content": [
                                            {
                                                "component": "VCardTitle",
                                                "props": {
                                                    "class": "d-flex align-center"
                                                },
                                                "content": [
                                                    {
                                                        "component": "VIcon",
                                                        "props": {
                                                            "icon": "mdi-folder-sync",
                                                            "class": "mr-2"
                                                        }
                                                    },
                                                    {
                                                        "component": "span",
                                                        "text": "路径配置"
                                                    }
                                                ]
                                            },
                                            {
                                                "component": "VCardText",
                                                "content": [
                                                    {
                                                        "component": "div",
                                                        "props": {
                                                            "class": "mb-2"
                                                        },
                                                        "content": [
                                                            {
                                                                "component": "VIcon",
                                                                "props": {
                                                                    "icon": "mdi-folder",
                                                                    "size": "small",
                                                                    "class": "mr-1"
                                                                }
                                                            },
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-caption"
                                                                },
                                                                "text": "根路径:"
                                                            },
                                                            {
                                                                "component": "div",
                                                                "text": self._sync_root_path or "未设置",
                                                                "props": {
                                                                    "class": "font-weight-medium text-truncate"
                                                                }
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        "component": "div",
                                                        "content": [
                                                            {
                                                                "component": "VIcon",
                                                                "props": {
                                                                    "icon": "mdi-folder-move",
                                                                    "size": "small",
                                                                    "class": "mr-1"
                                                                }
                                                            },
                                                            {
                                                                "component": "span",
                                                                "props": {
                                                                    "class": "text-caption"
                                                                },
                                                                "text": "目标路径:"
                                                            },
                                                            {
                                                                "component": "div",
                                                                "text": self._sync_target_path or "未设置",
                                                                "props": {
                                                                    "class": "font-weight-medium text-truncate"
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
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {
                                    "cols": 12
                                },
                                "content": [
                                    {
                                        "component": "VCard",
                                        "props": {
                                            "variant": "outlined"
                                        },
                                        "content": [
                                            {
                                                "component": "VCardTitle",
                                                "props": {
                                                    "class": "d-flex align-center"
                                                },
                                                "content": [
                                                    {
                                                        "component": "VIcon",
                                                        "props": {
                                                            "icon": "mdi-chart-line",
                                                            "class": "mr-2"
                                                        }
                                                    },
                                                    {
                                                        "component": "span",
                                                        "text": "统计信息"
                                                    }
                                                ]
                                            },
                                            {
                                                "component": "VCardText",
                                                "content": [
                                                    {
                                                        "component": "VRow",
                                                        "content": [
                                                            {
                                                                "component": "VCol",
                                                                "props": {
                                                                    "cols": 6,
                                                                    "md": 3
                                                                },
                                                                "content": [
                                                                    {
                                                                        "component": "VStatistic",
                                                                        "props": {
                                                                            "title": "总事件数",
                                                                            "value": stats_summary.get('total_events', 0),
                                                                            "color": "primary"
                                                                        }
                                                                    }
                                                                ]
                                                            },
                                                            {
                                                                "component": "VCol",
                                                                "props": {
                                                                    "cols": 6,
                                                                    "md": 3
                                                                },
                                                                "content": [
                                                                    {
                                                                        "component": "VStatistic",
                                                                        "props": {
                                                                            "title": "成功次数",
                                                                            "value": stats_summary.get('total_success', 0),
                                                                            "color": "success"
                                                                        }
                                                                    }
                                                                ]
                                                            },
                                                            {
                                                                "component": "VCol",
                                                                "props": {
                                                                    "cols": 6,
                                                                    "md": 3
                                                                },
                                                                "content": [
                                                                    {
                                                                        "component": "VStatistic",
                                                                        "props": {
                                                                            "title": "失败次数",
                                                                            "value": stats_summary.get('total_failed', 0),
                                                                            "color": "error"
                                                                        }
                                                                    }
                                                                ]
                                                            },
                                                            {
                                                                "component": "VCol",
                                                                "props": {
                                                                    "cols": 6,
                                                                    "md": 3
                                                                },
                                                                "content": [
                                                                    {
                                                                        "component": "VStatistic",
                                                                        "props": {
                                                                            "title": "成功率",
                                                                            "value": f"{stats_summary.get('success_rate', 0):.1f}%",
                                                                            "color": "info"
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
                            }
                        ]
                    }
                ]
            }
        ]

    def _get_stats_summary(self) -> Dict[str, Any]:
        """获取统计信息摘要"""
        total_events = 0
        total_success = 0
        total_failed = 0
        
        for event_stats in self._event_statistics.values():
            total_events += event_stats.get('total_count', 0)
            total_success += event_stats.get('success_count', 0)
            total_failed += event_stats.get('failed_count', 0)
        
        success_rate = (total_success / total_events * 100) if total_events > 0 else 0
        
        return {
            'total_events': total_events,
            'total_success': total_success,
            'total_failed': total_failed,
            'success_rate': success_rate
        }

    def _send_notification(self, title: str, text: str, image: str = None):
        """发送通知"""
        if not self._enable_notifications:
            return
        
        try:
            # 发送通知
            conf = NotificationConf(
                channel=self._notification_channels,
                title=title,
                text=text,
                image=image
            )
            self._notification_helper.send_notification_by_conf(conf)
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}")

    @cached(region="transfersync_dirs", ttl=60, skip_none=True)
    def _get_directories(self, path: str = "/") -> List[Dict[str, Any]]:
        """获取目录列表（带缓存）"""
        try:
            return self._storage_helper.get_directories(path)
        except Exception as e:
            logger.error(f"获取目录列表失败: {str(e)}")
            return []

    def _get_notification_options(self) -> List[Dict[str, str]]:
        """获取通知渠道选项"""
        notification_options = []
        try:
            configs = self._notification_helper.get_configs()
            notification_options = [
                {"title": config.name, "value": config.name}
                for config in configs.values()
                if config and hasattr(config, 'enabled') and config.enabled
            ]
        except Exception as e:
            logger.error(f"获取通知渠道失败: {str(e)}")
        return notification_options

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 获取可用的通知渠道
        notification_options = self._get_notification_options()

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
                                            'hint': '开启后插件将生效',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-subtitle-1 font-weight-bold'
                                },
                                'text': '路径配置'
                            },
                            {
                                'component': 'VCardText',
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
                                                        'component': 'VCombobox',
                                                        'props': {
                                                            'model': 'sync_root_path',
                                                            'label': '同步根路径',
                                                            'placeholder': '/media/downloads',
                                                            'hint': '整理完成后的文件所在根目录，可手动输入或从下拉列表选择',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-folder',
                                                            'append-inner-icon': 'mdi-folder-search',
                                                            'clearable': True,
                                                            'items': [
                                                                '/media/downloads',
                                                                '/media/movies',
                                                                '/media/tv',
                                                                '/data/media',
                                                                '/volume1/media',
                                                                '/mnt/media'
                                                            ],
                                                            'no-filter': False
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
                                                        'component': 'VCombobox',
                                                        'props': {
                                                            'model': 'sync_target_path',
                                                            'label': '同步目标路径',
                                                            'placeholder': '/media/backup',
                                                            'hint': '文件同步到的目标目录，可手动输入或从下拉列表选择',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-folder-move',
                                                            'append-inner-icon': 'mdi-folder-search',
                                                            'clearable': True,
                                                            'items': [
                                                                '/media/backup',
                                                                '/media/sync',
                                                                '/backup/media',
                                                                '/data/backup',
                                                                '/volume1/backup',
                                                                '/mnt/backup'
                                                            ],
                                                            'no-filter': False
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'class': 'mt-2'
                                        },
                                        'text': '提示：插件会监听根路径下的整理完成事件，自动将整理后的文件同步到目标路径'
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
                                                            'model': 'enable_immediate_execution',
                                                            'label': '启用立即执行',
                                                            'hint': '开启后整理完成立即同步，关闭则延迟执行',
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
                                                            'model': 'delay_minutes',
                                                            'label': '延迟执行时间（分钟）',
                                                            'type': 'number',
                                                            'hint': '整理完成后延迟多长时间执行同步',
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
                                                        'component': 'VBtn',
                                                        'props': {
                                                            'variant': 'outlined',
                                                            'color': 'primary'
                                                        },
                                                        'text': '立即执行同步'
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
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-subtitle-1 font-weight-bold'
                                },
                                'text': '同步设置'
                            },
                            {
                                'component': 'VCardText',
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
                                                            'hint': '选择文件同步策略',
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
                                                            'hint': '选择同步执行模式',
                                                            'persistent-hint': True
                                                        }
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
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-subtitle-1 font-weight-bold'
                                },
                                'text': '定时任务'
                            },
                            {
                                'component': 'VCardText',
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
                                                            'model': 'enable_incremental',
                                                            'label': '启用增量同步',
                                                            'hint': '定时检查并同步新增/更新的文件',
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
                                                        'component': 'VCombobox',
                                                        'props': {
                                                            'model': 'incremental_cron',
                                                            'label': '增量同步周期',
                                                            'placeholder': '0 */6 * * *',
                                                            'hint': 'Cron表达式，默认每6小时执行一次',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-clock-outline',
                                                            'clearable': True,
                                                            'items': [
                                                                {'title': '每小时', 'value': '0 * * * *'},
                                                                {'title': '每2小时', 'value': '0 */2 * * *'},
                                                                {'title': '每6小时', 'value': '0 */6 * * *'},
                                                                {'title': '每12小时', 'value': '0 */12 * * *'},
                                                                {'title': '每天凌晨2点', 'value': '0 2 * * *'},
                                                                {'title': '每天上午10点', 'value': '0 10 * * *'},
                                                                {'title': '每天下午6点', 'value': '0 18 * * *'}
                                                            ],
                                                            'item-title': 'title',
                                                            'item-value': 'value',
                                                            'no-filter': False
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
                                                            'hint': '定时执行完整的全量同步任务',
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
                                                        'component': 'VCombobox',
                                                        'props': {
                                                            'model': 'full_sync_cron',
                                                            'label': '全量同步周期',
                                                            'placeholder': '0 2 * * 0',
                                                            'hint': 'Cron表达式，默认每周日凌晨2点执行',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-calendar-clock',
                                                            'clearable': True,
                                                            'items': [
                                                                {'title': '每天凌晨3点', 'value': '0 3 * * *'},
                                                                {'title': '每周日凌晨2点', 'value': '0 2 * * 0'},
                                                                {'title': '每周一凌晨2点', 'value': '0 2 * * 1'},
                                                                {'title': '每月1号凌晨2点', 'value': '0 2 1 * *'},
                                                                {'title': '每周六凌晨4点', 'value': '0 4 * * 6'},
                                                                {'title': '每周三凌晨1点', 'value': '0 1 * * 3'}
                                                            ],
                                                            'item-title': 'title',
                                                            'item-value': 'value',
                                                            'no-filter': False
                                                        }
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
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-subtitle-1 font-weight-bold'
                                },
                                'text': '事件配置'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'trigger_events',
                                            'label': '触发事件',
                                            'items': [
                                                {'title': '整理完成', 'value': 'transfer_complete', 'subtitle': '推荐：媒体文件整理完成后触发'},
                                                {'title': '下载添加', 'value': 'download_added', 'subtitle': '下载任务添加时触发'},
                                                {'title': '订阅完成', 'value': 'subscribe_complete', 'subtitle': '订阅任务完成时触发'},
                                                {'title': '媒体添加', 'value': 'media_added', 'subtitle': '媒体库添加新内容时触发'},
                                                {'title': '文件移动', 'value': 'file_moved', 'subtitle': '文件移动操作时触发'},
                                                {'title': '目录扫描完成', 'value': 'directory_scan_complete', 'subtitle': '目录扫描完成时触发'},
                                                {'title': '刮削完成', 'value': 'scrape_complete', 'subtitle': '元数据刮削完成时触发'},
                                                {'title': '插件触发', 'value': 'plugin_triggered', 'subtitle': '其他插件触发时同步'}
                                            ],
                                            'multiple': True,
                                            'chips': True,
                                            'hint': '选择触发同步的事件类型（可多选）',
                                            'persistent-hint': True,
                                            'prepend-inner-icon': 'mdi-lightning-bolt'
                                        }
                                    },
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'event_conditions',
                                            'label': '事件过滤条件',
                                            'placeholder': 'key1=value1\nkey2=value2',
                                            'hint': '设置事件过滤条件，每行一个键值对（可选）',
                                            'persistent-hint': True,
                                            'rows': 3,
                                            'auto-grow': True,
                                            'prepend-inner-icon': 'mdi-filter-variant'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-subtitle-1 font-weight-bold'
                                },
                                'text': '文件过滤'
                            },
                            {
                                'component': 'VCardText',
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
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'file_filters',
                                                            'label': '文件类型过滤',
                                                            'placeholder': '.mp4,.mkv,.avi,.mov',
                                                            'hint': '只同步指定类型文件，用逗号分隔（留空同步所有）',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-file-multiple',
                                                            'clearable': True
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
                                                            'model': 'exclude_patterns',
                                                            'label': '排除模式',
                                                            'placeholder': '*.tmp,*.part,sample*',
                                                            'hint': '排除匹配模式的文件，支持通配符',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-minus-circle',
                                                            'clearable': True
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
                                                            'model': 'min_file_size',
                                                            'label': '最小文件大小（MB）',
                                                            'type': 'number',
                                                            'hint': '小于此大小的文件将被忽略（0表示无限制）',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-arrow-expand-down'
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
                                                            'model': 'max_file_size',
                                                            'label': '最大文件大小（MB）',
                                                            'type': 'number',
                                                            'hint': '大于此大小的文件将被忽略（0表示无限制）',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-arrow-expand-up'
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
                                                            'model': 'max_depth',
                                                            'label': '最大目录深度',
                                                            'type': 'number',
                                                            'hint': '限制扫描的目录层级（-1表示无限制）',
                                                            'persistent-hint': True,
                                                            'prepend-inner-icon': 'mdi-file-tree'
                                                        }
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
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-subtitle-1 font-weight-bold'
                                },
                                'text': '性能设置'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
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
                                                        'component': 'VSlider',
                                                        'props': {
                                                            'model': 'max_workers',
                                                            'label': '并发工作线程',
                                                            'min': 1,
                                                            'max': 16,
                                                            'step': 1,
                                                            'thumb-label': True,
                                                            'hint': '同时执行同步任务的线程数',
                                                            'persistent-hint': True,
                                                            'prepend-icon': 'mdi-cog'
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
                                                        'component': 'VSlider',
                                                        'props': {
                                                            'model': 'batch_size',
                                                            'label': '批处理大小',
                                                            'min': 10,
                                                            'max': 1000,
                                                            'step': 10,
                                                            'thumb-label': True,
                                                            'hint': '每批处理的文件数量',
                                                            'persistent-hint': True,
                                                            'prepend-icon': 'mdi-package-variant'
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
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'enable_progress',
                                                            'label': '显示进度信息',
                                                            'hint': '在日志中显示详细的进度信息',
                                                            'persistent-hint': True,
                                                            'color': 'primary'
                                                        }
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
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable_notifications',
                                            'label': '启用通知',
                                            'hint': '同步完成后发送通知',
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
                                            'model': 'notification_channels',
                                            'label': '通知渠道',
                                            'items': notification_options,
                                            'multiple': True,
                                            'chips': True,
                                            'hint': '选择通知渠道，留空使用所有可用渠道',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "sync_root_path": "",
            "sync_target_path": "",
            "delay_minutes": 5,
            "enable_immediate_execution": True,
            "sync_strategy": "copy",
            "sync_mode": "immediate",
            "enable_incremental": False,
            "incremental_cron": "0 */6 * * *",
            "enable_full_sync": False,
            "full_sync_cron": "0 2 * * 0",
            "trigger_events": ["transfer_complete"],
            "event_conditions": "",
            "file_filters": "",
            "exclude_patterns": "",
            "min_file_size": 0,
            "max_file_size": 0,
            "max_depth": -1,
            "max_workers": 4,
            "batch_size": 100,
            "enable_progress": True,
            "enable_notifications": False,
            "notification_channels": []
        }