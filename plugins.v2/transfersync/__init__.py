"""
整理后同步插件 - 重构版本
"""
import re
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

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

# 导入新的功能模块
from .api_handler import ApiHandler
from .notification_manager import NotificationManager
from .sync_scheduler import SyncScheduler
from .command_handler import CommandHandler
from .widget_manager import WidgetManager
from .workflow_actions import WorkflowActions


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
    _last_sync_time = None
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
            self._copy_paths = self._parse_paths(config.get("copy_paths", ""))
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

        # 初始化功能模块
        self.api_handler = ApiHandler(self)
        self.notification_manager = NotificationManager(self)
        self.sync_scheduler = SyncScheduler(self)
        self.command_handler = CommandHandler(self)
        self.widget_manager = WidgetManager(self)
        self.workflow_actions = WorkflowActions(self)

        # 注册事件监听器
        if self._enabled:
            self._register_event_listeners()
            # 设置定时任务
            self.sync_scheduler.setup_scheduler()

        logger.info("TransferSync插件初始化完成")

    def _parse_paths(self, paths_str: str) -> List[str]:
        """解析路径字符串"""
        if not paths_str:
            return []
        return [path.strip() for path in paths_str.split('\n') if path.strip()]

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

    def _validate_config(self):
        """验证配置"""
        if self._validator:
            validation_result = self._validator.validate_all_config(self._get_config_dict())
            if not validation_result.get('valid', True):
                logger.warning(f"配置验证警告: {validation_result.get('warnings', [])}")
                if validation_result.get('errors'):
                    logger.error(f"配置验证错误: {validation_result.get('errors', [])}")

    def get_state(self) -> bool:
        return self._enabled

    def stop_service(self):
        """停止服务"""
        try:
            # 停止调度器
            if hasattr(self, 'sync_scheduler'):
                self.sync_scheduler.shutdown()
            
            # 取消事件监听
            self._unregister_event_listeners()
            
            logger.info("TransferSync服务已停止")
        except Exception as e:
            logger.error(f"停止TransferSync服务失败: {str(e)}")

    def _get_config_dict(self) -> Dict:
        """获取配置字典"""
        return {
            "enabled": self._enabled,
            "copy_paths": '\n'.join(self._copy_paths),
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
        self._handle_event(event, TriggerEvent.PLUGIN_TRIGGERED)

    def _handle_event(self, event: Event, event_type: TriggerEvent):
        """统一事件处理方法"""
        if not self._should_handle_event(event, event_type):
            return

        try:
            start_time = datetime.now()
            sync_path = self._extract_sync_path(event.event_data, event_type)
            
            if sync_path:
                logger.info(f"处理{self._get_event_display_name(event_type.value)}事件，同步路径: {sync_path}")
                result = self._sync_ops.sync_directory(sync_path)
                success = result.get('success', False)
                error_type = result.get('error_type') if not success else None
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
        return self.api_handler.get_api()

    def get_command(self) -> List[Dict[str, Any]]:
        """获取插件命令"""
        return self.command_handler.get_command()

    def get_service(self) -> List[Dict[str, Any]]:
        """获取服务"""
        return [{
            "id": "transfersync",
            "name": "TransferSync同步服务",
            "trigger": "plugin",
            "api": self.get_api(),
            "widget": self.widget_manager.get_dashboard_widget()
        }]

    def get_actions(self) -> List[Dict[str, Any]]:
        """获取工作流动作"""
        return self.workflow_actions.get_actions()

    # 兼容性方法
    def _incremental_sync_job(self):
        """增量同步任务"""
        if hasattr(self, 'sync_scheduler'):
            self.sync_scheduler._incremental_sync_job()

    def _full_sync_job(self):
        """全量同步任务"""
        if hasattr(self, 'sync_scheduler'):
            self.sync_scheduler._full_sync_job()

    def _send_notification(self, title: str, text: str, image: str = None):
        """发送通知"""
        if hasattr(self, 'notification_manager'):
            self.notification_manager.send_notification(title, text, image)

    def _get_directories(self, path: str = "/") -> List[Dict[str, Any]]:
        """获取目录列表"""
        try:
            return self._storage_helper.get_directories(path)
        except Exception as e:
            logger.error(f"获取目录列表失败: {str(e)}")
            return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        # 获取可用的通知渠道
        notification_options = []
        try:
            if hasattr(self, 'notification_manager'):
                available_channels = self.notification_manager.get_available_channels()
                notification_options = [
                    {"title": info.get("name", name), "value": name}
                    for name, info in available_channels.items()
                    if info.get("enabled", False)
                ]
        except Exception as e:
            logger.error(f"获取通知渠道失败: {str(e)}")

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
                                            'label': '同步路径',
                                            'placeholder': '每行一个路径，支持绝对路径和相对路径',
                                            'hint': '设置需要同步的源路径，每行一个',
                                            'persistent-hint': True,
                                            'rows': 3
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
                                            'hint': '选择文件同步的策略',
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
                                            'hint': '定时执行增量同步任务',
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
                                            'label': '增量同步周期',
                                            'placeholder': '0 */6 * * *',
                                            'hint': '使用Cron表达式设置执行周期',
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
                                            'hint': '定时执行全量同步任务',
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
                                            'label': '全量同步周期',
                                            'placeholder': '0 2 * * 0',
                                            'hint': '使用Cron表达式设置执行周期',
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
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'trigger_events',
                                            'label': '触发事件',
                                            'items': [
                                                {'title': '整理完成', 'value': 'transfer_complete'},
                                                {'title': '下载添加', 'value': 'download_added'},
                                                {'title': '订阅完成', 'value': 'subscribe_complete'},
                                                {'title': '媒体添加', 'value': 'media_added'},
                                                {'title': '文件移动', 'value': 'file_moved'},
                                                {'title': '目录扫描完成', 'value': 'directory_scan_complete'},
                                                {'title': '刮削完成', 'value': 'scrape_complete'},
                                                {'title': '插件触发', 'value': 'plugin_triggered'}
                                            ],
                                            'multiple': True,
                                            'chips': True,
                                            'hint': '选择触发同步的事件类型',
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
            "copy_paths": "",
            "sync_strategy": "copy",
            "sync_mode": "immediate",
            "enable_incremental": False,
            "incremental_cron": "0 */6 * * *",
            "enable_full_sync": False,
            "full_sync_cron": "0 2 * * 0",
            "trigger_events": ["transfer_complete"],
            "enable_notifications": False,
            "notification_channels": []
        }