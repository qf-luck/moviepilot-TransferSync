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
                elif trigger_event == TriggerEvent.MEDIA_ADDED:
                    eventmanager.register(EventType.MediaAdded)(self._on_media_added)
                elif trigger_event == TriggerEvent.FILE_MOVED:
                    eventmanager.register(EventType.FileMoved)(self._on_file_moved)
                elif trigger_event == TriggerEvent.DIRECTORY_SCAN_COMPLETE:
                    eventmanager.register(EventType.DirectoryScanComplete)(self._on_directory_scan_complete)
                elif trigger_event == TriggerEvent.SCRAPE_COMPLETE:
                    eventmanager.register(EventType.ScrapeComplete)(self._on_scrape_complete)

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
                (EventType.PluginTriggered, self._on_plugin_triggered),
                (EventType.MediaAdded, self._on_media_added),
                (EventType.FileMoved, self._on_file_moved),
                (EventType.DirectoryScanComplete, self._on_directory_scan_complete),
                (EventType.ScrapeComplete, self._on_scrape_complete)
            ]

            for event_type, handler in event_handlers:
                try:
                    eventmanager.unregister(event_type, handler)
                except:
                    pass  # 忽略注销失败的错误

        except Exception as e:
            logger.error(f"注销事件监听器失败: {str(e)}")
            
    def _on_metadata_scrape(self, event: Event):
        """处理元数据刮削事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.METADATA_SCRAPE)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_webhook_message(self, event: Event):
        """处理Webhook消息事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.WEBHOOK_MESSAGE)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

    def _on_user_message(self, event: Event):
        """处理用户消息事件"""
        source_path = self._extract_sync_path(event.event_data, TriggerEvent.USER_MESSAGE)
        if source_path:
            self._sync_ops.sync_single_item(source_path)
        return True

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
        """拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构"""
        return [
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
                                'props': {'cols': 12},
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
                                'props': {'cols': 12, 'md': 6},
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
                                'props': {'cols': 12, 'md': 6},
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
                                'props': {'cols': 12, 'md': 6},
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
                                'props': {'cols': 12, 'md': 6},
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
                                'props': {'cols': 12, 'md': 6},
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
                                'props': {'cols': 12, 'md': 6},
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
            "notification_channels": ""
        }

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

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """定义远程控制命令"""
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
        """获取插件API接口"""
        api_list = [
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
        return api_list

    def sync_now(self) -> dict:
        """API接口：立即同步"""
        try:
            # 执行全量同步
            self._full_sync_job()
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

    def reset_event_statistics(self) -> dict:
        """API接口：重置事件统计"""
        try:
            with self._lock:
                self._event_statistics.clear()
            return {
                "code": 0,
                "message": "事件统计已重置"
            }
        except Exception as e:
            logger.error(f"重置统计失败: {str(e)}")
            return {
                "code": 1,
                "message": f"重置失败: {str(e)}"
            }

    def get_service(self) -> List[Dict[str, Any]]:
        """注册公共定时服务"""
        services = []

        if self._enable_incremental:
            services.append({
                "id": "transfersync_incremental", 
                "name": "整理后同步-增量同步",
                "trigger": CronTrigger.from_crontab(self._incremental_cron),
                "func": self._incremental_sync_job,
                "kwargs": {}
            })

        if self._enable_full_sync:
            services.append({
                "id": "transfersync_full",
                "name": "整理后同步-全量同步", 
                "trigger": CronTrigger.from_crontab(self._full_sync_cron),
                "func": self._full_sync_job,
                "kwargs": {}
            })

        return services

    def get_actions(self) -> List[Dict[str, Any]]:
        """获取插件工作流动作"""
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

    def sync_incremental_action(self, action_content) -> Tuple[bool, Any]:
        """工作流动作：增量同步"""
        try:
            self._incremental_sync_job()
            return True, "增量同步任务已启动"
        except Exception as e:
            logger.error(f"增量同步失败: {str(e)}")
            return False, str(e)

    def sync_full_action(self, action_content) -> Tuple[bool, Any]:
        """工作流动作：全量同步"""
        try:
            self._full_sync_job()
            return True, "全量同步任务已启动"
        except Exception as e:
            logger.error(f"全量同步失败: {str(e)}")
            return False, str(e)

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
            
            # 添加最后触发时间
            stats['last_triggered'] = stats.get('last_triggered', '从未触发')

        return self._event_statistics

    def _send_notification(self, title: str, text: str, image: str = None):
        """发送通知"""
        if not self._enable_notifications:
            return

        try:
            notification_helper = NotificationHelper()
            if self._notification_channels:
                # 指定渠道发送
                channels = self._notification_channels if isinstance(self._notification_channels, list) else [self._notification_channels]
                for channel in channels:
                    notification_helper.send_notification(
                        title=title,
                        text=text,
                        image=image,
                        channel=channel
                    )
            else:
                # 发送到所有渠道
                notification_helper.send_notification(
                    title=title,
                    text=text,
                    image=image
                )
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}")

    def _check_event_conditions(self, event_data: Dict, event_type: TriggerEvent) -> bool:
        """检查事件是否满足过滤条件"""
        if not self._event_conditions:
            return True

        try:
            conditions = self._event_conditions.get(event_type.value, {})
            if not conditions:
                return True

            # 媒体类型过滤
            if 'media_type' in conditions:
                expected_type = conditions['media_type'].lower()
                actual_type = str(event_data.get('type', '')).lower()
                if expected_type != actual_type and expected_type != 'all':
                    return False

            # 源路径过滤
            if 'source_path' in conditions:
                pattern = conditions['source_path']
                source_path = str(event_data.get('src', event_data.get('source_path', '')))
                if not re.search(pattern, source_path, re.IGNORECASE):
                    return False

            # 目标路径过滤
            if 'target_path' in conditions:
                pattern = conditions['target_path']
                target_path = str(event_data.get('dest', event_data.get('target_path', '')))
                if not re.search(pattern, target_path, re.IGNORECASE):
                    return False

            # 文件大小过滤
            if 'file_size' in conditions:
                condition_size = conditions['file_size']
                file_size = event_data.get('file_size', 0)
                if not self._match_condition(file_size, condition_size):
                    return False

            return True

        except Exception as e:
            logger.error(f"检查事件条件失败: {str(e)}")
            return True  # 条件检查失败时允许通过

    def _match_condition(self, value, condition_str: str) -> bool:
        """匹配条件值"""
        try:
            if isinstance(value, (int, float)):
                # 数值比较
                if condition_str.startswith('>='):
                    return value >= float(condition_str[2:])
                elif condition_str.startswith('<='):
                    return value <= float(condition_str[2:])
                elif condition_str.startswith('>'):
                    return value > float(condition_str[1:])
                elif condition_str.startswith('<'):
                    return value < float(condition_str[1:])
                elif condition_str.startswith('!='):
                    return value != float(condition_str[2:])
                else:
                    return value == float(condition_str)
            else:
                # 字符串比较
                return str(value).lower() == str(condition_str).lower()
        except:
            return True

    def _format_size(self, size_bytes: int) -> str:
        """格式化文件大小显示"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"

    def _get_sync_status(self) -> Dict[str, Any]:
        """获取同步状态信息"""
        try:
            if not self._sync_ops:
                return {
                    'status': SyncStatus.IDLE.value,
                    'message': '同步操作器未初始化',
                    'total_synced': 0,
                    'last_sync_time': None
                }

            current_status = getattr(self._sync_ops, 'current_status', SyncStatus.IDLE)
            sync_records = getattr(self._sync_ops, 'sync_records', {})
            
            return {
                'status': current_status.value if hasattr(current_status, 'value') else str(current_status),
                'message': f'已同步 {len(sync_records)} 个项目',
                'total_synced': len(sync_records),
                'last_sync_time': self._last_sync_time.isoformat() if self._last_sync_time else None,
                'enabled_paths': len(self._copy_paths),
                'trigger_events': [event.value for event in self._trigger_events]
            }
        except Exception as e:
            logger.error(f"获取同步状态失败: {str(e)}")
            return {
                'status': SyncStatus.ERROR.value,
                'message': f'获取状态失败: {str(e)}',
                'total_synced': 0,
                'last_sync_time': None
            }

    def clear_cache(self):
        """清理插件缓存"""
        try:
            # 清理事件统计缓存
            with self._lock:
                self._event_statistics.clear()
            
            # 清理同步操作器缓存
            if self._sync_ops and hasattr(self._sync_ops, 'clear_cache'):
                self._sync_ops.clear_cache()
            
            logger.info("插件缓存已清理")
            return True
        except Exception as e:
            logger.error(f"清理缓存失败: {str(e)}")
            return False

    def pause_sync(self):
        """暂停同步"""
        try:
            if self._sync_ops and hasattr(self._sync_ops, 'pause'):
                self._sync_ops.pause()
            logger.info("同步已暂停")
            return True
        except Exception as e:
            logger.error(f"暂停同步失败: {str(e)}")
            return False

    def resume_sync(self):
        """恢复同步"""
        try:
            if self._sync_ops and hasattr(self._sync_ops, 'resume'):
                self._sync_ops.resume()
            logger.info("同步已恢复")
            return True
        except Exception as e:
            logger.error(f"恢复同步失败: {str(e)}")
            return False

    def get_sync_status(self) -> Dict[str, Any]:
        """获取当前同步状态（公开方法）"""
        return self._get_sync_status()

    def _get_media_library_paths(self) -> List[Path]:
        """获取媒体库路径"""
        try:
            # 从配置或系统获取媒体库路径
            media_paths = []
            
            # 尝试从MoviePilot设置获取路径
            if hasattr(settings, 'LIBRARY_PATH') and settings.LIBRARY_PATH:
                media_paths.append(Path(settings.LIBRARY_PATH))
            
            # 添加用户配置的同步路径作为候选媒体库路径
            for path_str in self._copy_paths:
                path = Path(path_str)
                if path.exists() and path.is_dir():
                    media_paths.append(path)
            
            return media_paths
        except Exception as e:
            logger.error(f"获取媒体库路径失败: {str(e)}")
            return []

    def _get_filtered_files(self, path: Path, max_depth: int = -1, current_depth: int = 0) -> List[Path]:
        """获取过滤后的文件列表"""
        filtered_files = []
        
        try:
            if not path.exists() or not path.is_dir():
                return filtered_files
            
            # 检查深度限制
            if max_depth >= 0 and current_depth > max_depth:
                return filtered_files
            
            for item in path.iterdir():
                try:
                    if item.is_file():
                        # 应用文件过滤器
                        if self._should_sync_file(item):
                            filtered_files.append(item)
                    elif item.is_dir() and (max_depth < 0 or current_depth < max_depth):
                        # 递归处理子目录
                        sub_files = self._get_filtered_files(item, max_depth, current_depth + 1)
                        filtered_files.extend(sub_files)
                except PermissionError:
                    logger.warning(f"无权限访问: {item}")
                    continue
                except Exception as e:
                    logger.error(f"处理文件 {item} 时出错: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"获取过滤文件列表失败: {str(e)}")
            
        return filtered_files

    def _should_sync_file(self, file_path: Path) -> bool:
        """检查文件是否应该同步"""
        try:
            # 检查文件大小限制
            if file_path.exists():
                file_size_mb = file_path.stat().st_size / (1024 * 1024)
                
                if self._max_file_size > 0 and file_size_mb > self._max_file_size:
                    return False
                    
                if self._min_file_size > 0 and file_size_mb < self._min_file_size:
                    return False
            
            # 检查排除模式
            file_name = file_path.name.lower()
            for pattern in self._exclude_patterns:
                try:
                    if re.match(pattern.lower(), file_name):
                        return False
                except re.error:
                    # 如果正则表达式无效，尝试简单的通配符匹配
                    import fnmatch
                    if fnmatch.fnmatch(file_name, pattern.lower()):
                        return False
            
            return True
            
        except Exception as e:
            logger.error(f"检查文件同步条件失败 {file_path}: {str(e)}")
            return True  # 出错时默认允许同步