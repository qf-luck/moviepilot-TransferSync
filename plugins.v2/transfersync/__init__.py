"""
整理后同步插件 - 重构版本
"""
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
from app.schemas.types import EventType, NotificationType
from app.schemas import NotificationConf
from app.helper.notification import NotificationHelper
from app.helper.mediaserver import MediaServerHelper

# 导入模块
from .types import SyncStrategy, SyncMode, FileFilterType, SyncStatus, TriggerEvent, EventCondition
from .exceptions import SyncException, SyncPermissionError, SyncSpaceError, SyncNetworkError
from .file_operations import AtomicFileOperation
from .config_validator import ConfigValidator
from .event_handler import event_handler
from .sync_operations import SyncOperations


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
    _scheduler = None
    _last_sync_time = None
    _validator = None
    _sync_ops = None
    _lock = threading.Lock()

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        if config:
            self._enabled = config.get("enabled", False)
            
            # 处理copy_paths：支持字符串（多行）或列表格式
            copy_paths_config = config.get("copy_paths", "")
            if isinstance(copy_paths_config, str):
                self._copy_paths = [path.strip() for path in copy_paths_config.split('\n') if path.strip()]
            elif isinstance(copy_paths_config, list):
                self._copy_paths = copy_paths_config
            else:
                self._copy_paths = []
            
            self._enable_incremental = config.get("enable_incremental", False)
            self._incremental_cron = config.get("incremental_cron", "0 */6 * * *")
            self._enable_full_sync = config.get("enable_full_sync", False)
            self._full_sync_cron = config.get("full_sync_cron", "0 2 * * 0")
            self._enable_notifications = config.get("enable_notifications", False)
            self._notification_channels = config.get("notification_channels", [])
            
            # 安全地处理枚举值
            try:
                self._sync_strategy = SyncStrategy(config.get("sync_strategy", SyncStrategy.COPY.value))
            except ValueError:
                self._sync_strategy = SyncStrategy.COPY
                
            try:
                self._sync_mode = SyncMode(config.get("sync_mode", SyncMode.IMMEDIATE.value))
            except ValueError:
                self._sync_mode = SyncMode.IMMEDIATE
            
            self._max_depth = config.get("max_depth", -1)
            self._file_filters = config.get("file_filters", [])
            self._exclude_patterns = config.get("exclude_patterns", [])
            self._max_file_size = config.get("max_file_size", 0)
            self._min_file_size = config.get("min_file_size", 0)
            self._enable_progress = config.get("enable_progress", True)
            self._max_workers = config.get("max_workers", 4)
            self._batch_size = config.get("batch_size", 100)
            
            # 处理触发事件配置
            trigger_events_config = config.get("trigger_events", [])
            if trigger_events_config:
                event_values = trigger_events_config if isinstance(trigger_events_config, list) else [trigger_events_config]
                try:
                    self._trigger_events = [TriggerEvent(val) for val in event_values if self._is_valid_event(val)]
                except Exception:
                    self._trigger_events = [TriggerEvent.TRANSFER_COMPLETE]
            else:
                self._trigger_events = [TriggerEvent.TRANSFER_COMPLETE]
            
            self._event_conditions = config.get("event_conditions", {})

        # 初始化验证器和同步操作器
        try:
            self._validator = ConfigValidator()
            self._sync_ops = SyncOperations(self._get_config_dict())
        except Exception as e:
            logger.error(f"初始化插件组件失败: {str(e)}")
            self._validator = None
            self._sync_ops = None
        
        # 启用插件时注册事件监听
        if self._enabled:
            try:
                self._register_event_listeners()
                self._setup_scheduler()
            except Exception as e:
                logger.error(f"启用插件失败: {str(e)}")
                self._enabled = False

    def get_state(self) -> bool:
        """获取插件状态"""
        return self._enabled

    def stop_service(self):
        """停止插件服务"""
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"停止调度器失败: {str(e)}")
    
    def _get_config_dict(self) -> Dict:
        """获取配置字典"""
        return {
            'copy_paths': self._copy_paths,
            'sync_strategy': self._sync_strategy,
            'sync_mode': self._sync_mode,
            'max_depth': self._max_depth,
            'file_filters': self._file_filters,
            'exclude_patterns': self._exclude_patterns,
            'max_file_size': self._max_file_size,
            'min_file_size': self._min_file_size,
            'max_workers': self._max_workers,
            'batch_size': self._batch_size
        }

    def _register_event_listeners(self):
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
        
        for trigger_event in self._trigger_events:
            handler = handler_map.get(trigger_event)
            if handler:
                # 使用装饰器注册事件处理器
                decorated_handler = event_handler(trigger_event)(handler)
                setattr(self, f'_decorated_{trigger_event.value}', decorated_handler)
                
                # 注册到事件管理器
                try:
                    eventmanager.register_event(EventType.to_event_type(trigger_event.value), decorated_handler)
                    logger.info(f"已注册{self._get_event_display_name(trigger_event.value)}事件监听器")
                except Exception as e:
                    logger.warning(f"注册{trigger_event.value}事件监听器失败: {str(e)}")

    def _setup_scheduler(self):
        """设置定时任务调度器"""
        if self._scheduler is None:
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            
        try:
            # 增量同步任务
            if self._enable_incremental and self._incremental_cron:
                self._scheduler.add_job(
                    func=self._incremental_sync_job,
                    trigger=CronTrigger.from_crontab(self._incremental_cron),
                    id="transfersync_incremental",
                    name="转移同步增量任务",
                    replace_existing=True
                )
                
            # 全量同步任务  
            if self._enable_full_sync and self._full_sync_cron:
                self._scheduler.add_job(
                    func=self._full_sync_job,
                    trigger=CronTrigger.from_crontab(self._full_sync_cron),
                    id="transfersync_full",
                    name="转移同步全量任务",
                    replace_existing=True
                )
                
            if not self._scheduler.running:
                self._scheduler.start()
                logger.info("转移同步调度器已启动")
                
        except Exception as e:
            logger.error(f"设置调度器失败: {str(e)}")

    # 事件处理方法
    def _on_transfer_complete(self, event: Event):
        """处理整理完成事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.TRANSFER_COMPLETE)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_download_added(self, event: Event):
        """处理下载添加事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.DOWNLOAD_ADDED)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_subscribe_complete(self, event: Event):
        """处理订阅完成事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.SUBSCRIBE_COMPLETE)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_media_added(self, event: Event):
        """处理媒体添加事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.MEDIA_ADDED)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_file_moved(self, event: Event):
        """处理文件移动事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.FILE_MOVED)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_directory_scan_complete(self, event: Event):
        """处理目录扫描完成事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.DIRECTORY_SCAN_COMPLETE)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_scrape_complete(self, event: Event):
        """处理刮削完成事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.SCRAPE_COMPLETE)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_plugin_triggered(self, event: Event):
        """处理插件触发事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.PLUGIN_TRIGGERED)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _extract_sync_path(self, event_data: Dict, event_type: TriggerEvent) -> Optional[str]:
        """从事件数据中提取同步路径"""
        if not event_data:
            return None
            
        # 尝试多个可能的路径键
        path_keys = ['path', 'filepath', 'source_path', 'target_path', 'dest_path', 'file_path']
        
        for key in path_keys:
            if key in event_data:
                path_value = event_data[key]
                if isinstance(path_value, str) and path_value.strip():
                    return path_value.strip()
                elif isinstance(path_value, Path):
                    return str(path_value)
        
        logger.debug(f"未能从{event_type.value}事件数据中提取路径: {event_data}")
        return None

    def _should_handle_event(self, event: Event, event_type: TriggerEvent) -> bool:
        """检查是否应该处理该事件"""
        if not self._enabled:
            return False
            
        if event_type not in self._trigger_events:
            return False
            
        # 检查事件过滤条件
        conditions = self._event_conditions.get(event_type.value, {})
        if conditions:
            # 实现具体的条件检查逻辑
            pass
            
        return True

    def _get_event_display_name(self, event_value: str) -> str:
        """获取事件显示名称"""
        display_names = TriggerEvent.get_display_names()
        return display_names.get(event_value, event_value)

    def _is_valid_event(self, event_value: str) -> bool:
        """检查事件值是否有效"""
        try:
            TriggerEvent(event_value)
            return True
        except ValueError:
            return False

    def _update_event_stats(self, event_type: TriggerEvent, success: bool, processing_time: float, error_type: str = None):
        """更新事件统计信息"""
        with self._lock:
            stats = self._event_statistics.setdefault(event_type.value, {
                'total_count': 0,
                'success_count': 0,
                'error_count': 0,
                'total_time': 0.0,
                'avg_time': 0.0,
                'error_types': {}
            })
            
            stats['total_count'] += 1
            stats['total_time'] += processing_time
            stats['avg_time'] = stats['total_time'] / stats['total_count']
            
            if success:
                stats['success_count'] += 1
            else:
                stats['error_count'] += 1
                if error_type:
                    stats['error_types'][error_type] = stats['error_types'].get(error_type, 0) + 1

    def _incremental_sync_job(self):
        """增量同步任务"""
        logger.info("开始执行增量同步任务")
        # 实现增量同步逻辑
        pass

    def _full_sync_job(self):
        """全量同步任务"""
        logger.info("开始执行全量同步任务")
        # 实现全量同步逻辑
        pass

    @eventmanager.register(EventType.PluginAction)
    def remote_sync(self, event: Event):
        """远程同步"""
        if event:
            logger.info("收到远程同步请求")
            event_info = event.event_data
            if not event_info or event_info.get("action") != "sync":
                return
            args = event_info.get("args")
            if args:
                logger.info(f"远程同步参数: {args}")
                self._sync_ops.sync_single_item(args)
            else:
                logger.warning("远程同步缺少参数")

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取插件配置表单"""
        try:
            # 获取事件选项
            event_options = [{"title": name, "value": value} 
                            for value, name in TriggerEvent.get_display_names().items()]
            
            # 获取同步策略选项
            strategy_options = [
                {"title": "复制", "value": SyncStrategy.COPY.value},
                {"title": "移动", "value": SyncStrategy.MOVE.value},
                {"title": "硬链接", "value": SyncStrategy.HARDLINK.value},
                {"title": "软链接", "value": SyncStrategy.SOFTLINK.value}
            ]
            
            # 获取同步模式选项
            mode_options = [
                {"title": "立即同步", "value": SyncMode.IMMEDIATE.value},
                {"title": "批量同步", "value": SyncMode.BATCH.value},
                {"title": "队列同步", "value": SyncMode.QUEUE.value}
            ]
        except Exception as e:
            logger.error(f"获取表单选项失败: {str(e)}")
            # 提供默认选项
            event_options = [{"title": "整理完成", "value": "transfer.complete"}]
            strategy_options = [{"title": "复制", "value": "copy"}]
            mode_options = [{"title": "立即同步", "value": "immediate"}]

        elements = [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enabled',
                                            'label': '启用插件',
                                            'hint': '开启后将监听选定的事件类型并自动同步文件',
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
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextarea',
                                        'props': {
                                            'model': 'copy_paths',
                                            'label': '同步目标路径',
                                            'placeholder': '每行一个路径，支持多个目标路径',
                                            'hint': '文件将被同步到这些目录中',
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
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'trigger_events',
                                            'label': '触发事件',
                                            'multiple': True,
                                            'chips': True,
                                            'items': event_options,
                                            'hint': '选择触发同步的事件类型',
                                            'persistent-hint': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'sync_strategy',
                                            'label': '同步策略',
                                            'items': strategy_options,
                                            'hint': '选择文件同步方式',
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

        try:
            default_values = {
                "enabled": False,
                "copy_paths": "",
                "trigger_events": [TriggerEvent.TRANSFER_COMPLETE.value],
                "sync_strategy": SyncStrategy.COPY.value
            }
        except Exception:
            default_values = {
                "enabled": False,
                "copy_paths": "",
                "trigger_events": ["transfer.complete"],
                "sync_strategy": "copy"
            }
        
        return elements, default_values

    def get_page(self) -> List[dict]:
        """获取插件状态页面"""
        try:
            # 获取同步统计信息
            if self._sync_ops:
                current_status = getattr(self._sync_ops, 'current_status', None)
                status_text = current_status.value if current_status else "未知"
                stats_count = len(getattr(self._sync_ops, 'sync_records', {}))
            else:
                status_text = "未初始化"
                stats_count = 0
                
            return [
                {
                    'component': 'div',
                    'content': [
                        {
                            'component': 'VCard',
                            'props': {'class': 'mb-3'},
                            'content': [
                                {
                                    'component': 'VCardTitle',
                                    'props': {'class': 'text-h6'},
                                    'content': '同步状态'
                                },
                                {
                                    'component': 'VCardText',
                                    'content': f'当前状态: {status_text}'
                                },
                                {
                                    'component': 'VCardText',
                                    'content': f'同步记录数: {stats_count}'
                                }
                            ]
                        }
                    ]
                }
            ]
        except Exception as e:
            logger.error(f"获取状态页面失败: {str(e)}")
            return [
                {
                    'component': 'VCard',
                    'content': [
                        {
                            'component': 'VCardTitle',
                            'content': '插件状态'
                        },
                        {
                            'component': 'VCardText',
                            'content': '插件正在加载中...'
                        }
                    ]
                }
            ]

    def sync_single_action(self, action_content) -> Tuple[bool, Any]:
        """手动同步单个项目"""
        if not action_content:
            return False, "缺少同步路径参数"
        
        try:
            self._sync_ops.sync_single_item(action_content)
            return True, f"同步任务已启动: {action_content}"
        except Exception as e:
            logger.error(f"手动同步失败: {str(e)}")
            return False, str(e)