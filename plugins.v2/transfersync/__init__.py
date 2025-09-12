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
    _cache_manager = None
    _local_cache = TTLCache(maxsize=100, ttl=300)  # 5分钟TTL本地缓存
    _performance_metrics = {}  # 性能监控指标
    _health_status = {"status": "unknown", "checks": {}}  # 健康检查状态
    _backup_manager = None  # 备份管理器

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
        
        # 初始化缓存管理器为None (系统级缓存暂不可用)
        self._cache_manager = None
        
        # 清理旧的缓存数据
        self._cleanup_cache()
        
        # 获取可用的通知渠道配置
        self._notification_configs = self._notification_helper.get_configs()
        self._available_channels = list(self._notification_configs.keys()) if self._notification_configs else []

        # 初始化同步操作器
        try:
            self._sync_ops = SyncOperations(self._get_config_dict())
        except Exception as e:
            logger.error(f"初始化同步操作器失败: {str(e)}")
            self._sync_ops = None

        # 启用插件时的额外初始化
        if self._enabled:
            try:
                # 注册事件监听器
                self._register_event_listeners()
                # 设置定时任务
                self._setup_scheduler()
            except Exception as e:
                logger.error(f"启用插件失败: {str(e)}")
                self._enabled = False
                
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
        
    def _parse_event_conditions(self, conditions_str: str) -> Dict[str, Any]:
        """解析事件过滤条件配置"""
        if isinstance(conditions_str, str) and conditions_str.strip():
            try:
                # 简单的键值对解析，格式: event_type:condition=value,event_type2:condition=value
                conditions = {}
                pairs = conditions_str.split(',')
                for pair in pairs:
                    if ':' in pair:
                        event_type, condition = pair.split(':', 1)
                        conditions[event_type.strip()] = condition.strip()
                return conditions
            except Exception as e:
                logger.error(f"解析事件条件失败: {str(e)}")
                return {}
        return conditions_str if isinstance(conditions_str, dict) else {}

    def _validate_config(self):
        """验证配置的基本有效性"""
        try:
            # 验证路径
            if not self._copy_paths:
                logger.warning("未配置同步目标路径")
            
            # 验证Cron表达式
            if self._enable_incremental and self._incremental_cron:
                try:
                    CronTrigger.from_crontab(self._incremental_cron)
                except Exception as e:
                    logger.error(f"增量同步Cron表达式无效: {e}")
                    self._enable_incremental = False
                    
            if self._enable_full_sync and self._full_sync_cron:
                try:
                    CronTrigger.from_crontab(self._full_sync_cron)
                except Exception as e:
                    logger.error(f"全量同步Cron表达式无效: {e}")
                    self._enable_full_sync = False
                    
            # 验证数值配置
            if self._max_workers < 1:
                self._max_workers = 4
            if self._batch_size < 1:
                self._batch_size = 100
                
            logger.info("配置验证完成")
        except Exception as e:
            logger.error(f"配置验证失败: {str(e)}")

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
        """远程同步和交互式命令处理"""
        if not event:
            return
            
        logger.info("收到远程同步请求")
        event_info = event.event_data
        if not event_info:
            return
            
        action = event_info.get("action")
        args = event_info.get("args")
        
        try:
            if action == "transfersync" or action == "sync":
                # 基本同步命令
                if args:
                    logger.info(f"远程同步参数: {args}")
                    self._sync_ops.sync_single_item(args)
                    self._send_interactive_response(event, f"✅ 同步任务已启动: {args}")
                else:
                    # 显示同步选项菜单
                    self._send_sync_menu(event)
                    
            elif action == "sync_status":
                # 查看同步状态
                self._send_sync_status(event)
                
            elif action == "sync_quick":
                # 快速同步
                self._handle_quick_sync(event, args)
                
            elif action == "sync_config":
                # 同步配置管理
                self._send_config_menu(event)
                
            elif action.startswith("sync_action_"):
                # 处理按钮回调
                self._handle_button_callback(event, action, args)
                
        except Exception as e:
            logger.error(f"处理交互式命令失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 操作失败: {str(e)}")

    def _send_interactive_response(self, event: Event, message: str, buttons: List[Dict] = None):
        """发送交互式响应消息"""
        try:
            response_data = {
                "text": message,
                "userid": event.event_data.get("userid"),
                "username": event.event_data.get("username")
            }
            
            if buttons:
                response_data["buttons"] = buttons
                
            # 通过事件发送响应
            eventmanager.send_event(
                EventType.NotificationMessage,
                response_data
            )
        except Exception as e:
            logger.error(f"发送交互式响应失败: {str(e)}")
    
    def _send_sync_menu(self, event: Event):
        """发送同步选项菜单"""
        buttons = [
            {
                "text": "🔄 立即同步",
                "callback_data": {
                    "action": "sync_action_immediate",
                    "args": "all"
                }
            },
            {
                "text": "📊 查看状态", 
                "callback_data": {
                    "action": "sync_status",
                    "args": ""
                }
            },
            {
                "text": "⚙️ 配置管理",
                "callback_data": {
                    "action": "sync_config", 
                    "args": ""
                }
            },
            {
                "text": "📈 统计信息",
                "callback_data": {
                    "action": "sync_action_stats",
                    "args": ""
                }
            }
        ]
        
        message = "🔄 **整理后同步 - 操作菜单**\n\n请选择要执行的操作："
        self._send_interactive_response(event, message, buttons)
    
    def _send_sync_status(self, event: Event):
        """发送同步状态信息"""
        try:
            status_msg = "📊 **同步状态信息**\n\n"
            
            # 基本状态
            status_msg += f"🔹 插件状态: {'✅ 已启用' if self._enabled else '❌ 已禁用'}\n"
            status_msg += f"🔹 同步路径数: {len(self._copy_paths)}\n"
            status_msg += f"🔹 同步策略: {self._sync_strategy.value}\n"
            status_msg += f"🔹 同步模式: {self._sync_mode.value}\n"
            
            # 最后同步时间
            if self._last_sync_time:
                status_msg += f"🔹 上次同步: {self._last_sync_time.strftime('%Y-%m-%d %H:%M:%S')}\n"
            else:
                status_msg += f"🔹 上次同步: 暂无记录\n"
                
            # 事件统计
            if self._event_statistics:
                status_msg += f"\n📈 **事件统计**\n"
                for event_type, count in self._event_statistics.items():
                    status_msg += f"▫️ {event_type}: {count} 次\n"
            
            buttons = [
                {
                    "text": "🔄 刷新状态",
                    "callback_data": {
                        "action": "sync_status",
                        "args": ""
                    }
                },
                {
                    "text": "🏠 返回菜单",
                    "callback_data": {
                        "action": "transfersync",
                        "args": ""
                    }
                }
            ]
            
            self._send_interactive_response(event, status_msg, buttons)
            
        except Exception as e:
            logger.error(f"获取同步状态失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 获取状态失败: {str(e)}")
    
    def _handle_quick_sync(self, event: Event, args: str):
        """处理快速同步"""
        try:
            if not self._enabled:
                self._send_interactive_response(event, "❌ 插件未启用，无法执行同步")
                return
                
            if not self._copy_paths:
                self._send_interactive_response(event, "❌ 未配置同步路径")
                return
            
            # 执行快速同步
            self._send_interactive_response(event, "🔄 正在执行快速同步...")
            
            # 这里可以根据args参数执行不同类型的快速同步
            if args == "incremental":
                self._execute_incremental_sync()
                self._send_interactive_response(event, "✅ 增量同步已完成")
            else:
                # 执行全量同步
                self._execute_full_sync() 
                self._send_interactive_response(event, "✅ 全量同步已完成")
                
        except Exception as e:
            logger.error(f"快速同步失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 快速同步失败: {str(e)}")
    
    def _send_config_menu(self, event: Event):
        """发送配置管理菜单"""
        buttons = [
            {
                "text": "🔧 查看配置",
                "callback_data": {
                    "action": "sync_action_view_config",
                    "args": ""
                }
            },
            {
                "text": "🎯 测试连接",
                "callback_data": {
                    "action": "sync_action_test_config",
                    "args": ""
                }
            },
            {
                "text": "🗑️ 重置统计",
                "callback_data": {
                    "action": "sync_action_reset_stats", 
                    "args": ""
                }
            },
            {
                "text": "🏠 返回菜单",
                "callback_data": {
                    "action": "transfersync",
                    "args": ""
                }
            }
        ]
        
        message = "⚙️ **配置管理菜单**\n\n请选择配置操作："
        self._send_interactive_response(event, message, buttons)
    
    def _handle_button_callback(self, event: Event, action: str, args: str):
        """处理按钮回调"""
        try:
            if action == "sync_action_immediate":
                self._handle_quick_sync(event, "full")
                
            elif action == "sync_action_stats":
                self._send_stats_info(event)
                
            elif action == "sync_action_view_config":
                self._send_config_info(event)
                
            elif action == "sync_action_test_config":
                self._test_config(event)
                
            elif action == "sync_action_reset_stats":
                self._reset_stats(event)
                
        except Exception as e:
            logger.error(f"处理按钮回调失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 操作失败: {str(e)}")
    
    def _send_stats_info(self, event: Event):
        """发送统计信息"""
        try:
            stats_msg = "📈 **详细统计信息**\n\n"
            
            if self._event_statistics:
                total_events = sum(self._event_statistics.values())
                stats_msg += f"🔹 总事件数: {total_events}\n\n"
                
                for event_type, count in sorted(self._event_statistics.items()):
                    percentage = (count / total_events * 100) if total_events > 0 else 0
                    stats_msg += f"▫️ {event_type}: {count} 次 ({percentage:.1f}%)\n"
            else:
                stats_msg += "暂无统计数据"
            
            buttons = [
                {
                    "text": "🏠 返回菜单",
                    "callback_data": {
                        "action": "transfersync",
                        "args": ""
                    }
                }
            ]
            
            self._send_interactive_response(event, stats_msg, buttons)
            
        except Exception as e:
            logger.error(f"获取统计信息失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 获取统计失败: {str(e)}")
    
    def _send_config_info(self, event: Event):
        """发送配置信息"""
        try:
            config_msg = "🔧 **当前配置信息**\n\n"
            config_msg += f"🔹 启用状态: {'✅ 已启用' if self._enabled else '❌ 已禁用'}\n"
            config_msg += f"🔹 同步策略: {self._sync_strategy.value}\n"
            config_msg += f"🔹 同步模式: {self._sync_mode.value}\n"
            config_msg += f"🔹 最大工作线程: {self._max_workers}\n"
            config_msg += f"🔹 批次大小: {self._batch_size}\n"
            config_msg += f"🔹 增量同步: {'✅ 启用' if self._enable_incremental else '❌ 禁用'}\n"
            config_msg += f"🔹 全量同步: {'✅ 启用' if self._enable_full_sync else '❌ 禁用'}\n"
            config_msg += f"🔹 通知推送: {'✅ 启用' if self._enable_notifications else '❌ 禁用'}\n"
            
            if self._copy_paths:
                config_msg += f"\n📁 **同步路径配置**\n"
                for i, path_config in enumerate(self._copy_paths[:3], 1):  # 只显示前3个
                    source = path_config.get('source_path', '')
                    target = path_config.get('target_path', '')
                    config_msg += f"▫️ {i}. {source} → {target}\n"
                
                if len(self._copy_paths) > 3:
                    config_msg += f"... 还有 {len(self._copy_paths) - 3} 个路径\n"
            
            buttons = [
                {
                    "text": "🏠 返回菜单", 
                    "callback_data": {
                        "action": "transfersync",
                        "args": ""
                    }
                }
            ]
            
            self._send_interactive_response(event, config_msg, buttons)
            
        except Exception as e:
            logger.error(f"获取配置信息失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 获取配置失败: {str(e)}")
    
    def _test_config(self, event: Event):
        """测试配置"""
        try:
            self._send_interactive_response(event, "🔍 正在测试配置...")
            
            # 测试路径访问性
            accessible_paths = 0
            total_paths = len(self._copy_paths)
            
            for path_config in self._copy_paths:
                source = path_config.get('source_path', '')
                target = path_config.get('target_path', '')
                
                if Path(source).exists() and Path(target).parent.exists():
                    accessible_paths += 1
            
            success_rate = (accessible_paths / total_paths * 100) if total_paths > 0 else 0
            
            if success_rate == 100:
                result_msg = f"✅ **配置测试通过**\n\n所有 {total_paths} 个路径都可正常访问"
            elif success_rate >= 50:
                result_msg = f"⚠️ **配置部分通过**\n\n{accessible_paths}/{total_paths} 个路径可访问 ({success_rate:.1f}%)"
            else:
                result_msg = f"❌ **配置测试失败**\n\n仅 {accessible_paths}/{total_paths} 个路径可访问 ({success_rate:.1f}%)"
            
            buttons = [
                {
                    "text": "🏠 返回菜单",
                    "callback_data": {
                        "action": "transfersync", 
                        "args": ""
                    }
                }
            ]
            
            self._send_interactive_response(event, result_msg, buttons)
            
        except Exception as e:
            logger.error(f"测试配置失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 配置测试失败: {str(e)}")
    
    def _reset_stats(self, event: Event):
        """重置统计数据"""
        try:
            self._event_statistics = {}
            self._send_interactive_response(event, "✅ 统计数据已重置")
        except Exception as e:
            logger.error(f"重置统计失败: {str(e)}")
            self._send_interactive_response(event, f"❌ 重置失败: {str(e)}")

    def _cleanup_cache(self):
        """清理旧的缓存数据"""
        try:
            # 清理插件相关的缓存键
            cache_keys = [
                "transfersync_sync_status",
                "transfersync_event_stats", 
                "transfersync_config_validation",
                "transfersync_storage_list",
                "transfersync_notification_channels"
            ]
            
            for key in cache_keys:
                if self._cache_manager:
                    self._cache_manager.delete(key)
                    
            # 清理本地缓存
            self._local_cache.clear()
            
            logger.debug("缓存清理完成")
            
        except Exception as e:
            logger.warning(f"缓存清理失败: {str(e)}")

    @cached(cache=TTLCache(maxsize=1, ttl=60), key=lambda self: "transfersync_sync_status")
    def _get_cached_sync_status(self) -> Dict[str, Any]:
        """获取缓存的同步状态"""
        try:
            status_data = {
                "enabled": self._enabled,
                "sync_paths": len(self._copy_paths),
                "last_sync": self._last_sync_time.isoformat() if self._last_sync_time else None,
                "sync_strategy": self._sync_strategy.value,
                "sync_mode": self._sync_mode.value,
                "total_events": sum(self._event_statistics.values()) if self._event_statistics else 0
            }
            
            # 使用系统级缓存存储
            if self._cache_manager:
                self._cache_manager.set("transfersync_sync_status", status_data, ttl=60)
                
            return status_data
            
        except Exception as e:
            logger.error(f"获取缓存同步状态失败: {str(e)}")
            return {}

    @cached(cache=TTLCache(maxsize=1, ttl=300), key=lambda self: "transfersync_event_stats")
    def _get_cached_event_statistics(self) -> Dict[str, Any]:
        """获取缓存的事件统计"""
        try:
            stats_data = dict(self._event_statistics) if self._event_statistics else {}
            
            # 添加统计摘要
            stats_summary = {
                "total_events": sum(stats_data.values()),
                "event_types": len(stats_data),
                "most_frequent": max(stats_data.items(), key=lambda x: x[1])[0] if stats_data else None,
                "statistics": stats_data
            }
            
            # 使用系统级缓存存储
            if self._cache_manager:
                self._cache_manager.set("transfersync_event_stats", stats_summary, ttl=300)
                
            return stats_summary
            
        except Exception as e:
            logger.error(f"获取缓存事件统计失败: {str(e)}")
            return {"total_events": 0, "event_types": 0, "statistics": {}}

    @cached(cache=TTLCache(maxsize=1, ttl=600), key=lambda self: "transfersync_storage_list")
    def _get_cached_storage_list(self) -> List[Dict[str, Any]]:
        """获取缓存的存储列表"""
        try:
            if not self._storage_helper:
                return []
                
            storages = self._storage_helper.get_storages()
            storage_list = []
            
            for storage in storages:
                storage_info = {
                    "name": storage.name,
                    "type": storage.type,
                    "enabled": storage.enabled,
                    "path": storage.config.get("path", "") if storage.config else "",
                    "available": True  # 这里可以添加可用性检查
                }
                storage_list.append(storage_info)
            
            # 使用系统级缓存存储
            if self._cache_manager:
                self._cache_manager.set("transfersync_storage_list", storage_list, ttl=600)
                
            return storage_list
            
        except Exception as e:
            logger.error(f"获取缓存存储列表失败: {str(e)}")
            return []

    def _cache_config_validation(self, config: dict) -> Dict[str, Any]:
        """缓存配置验证结果"""
        try:
            config_hash = hash(str(sorted(config.items())))
            cache_key = f"transfersync_config_validation_{config_hash}"
            
            # 检查本地缓存
            if cache_key in self._local_cache:
                return self._local_cache[cache_key]
                
            # 检查系统级缓存
            if self._cache_manager:
                cached_result = self._cache_manager.get(cache_key)
                if cached_result:
                    self._local_cache[cache_key] = cached_result
                    return cached_result
            
            # 执行验证
            if not self._validator:
                self._validator = ConfigValidator()
                
            validation_result = self._validator.validate_config(config)
            
            # 存储到缓存
            self._local_cache[cache_key] = validation_result
            if self._cache_manager:
                self._cache_manager.set(cache_key, validation_result, ttl=1800)  # 30分钟
                
            return validation_result
            
        except Exception as e:
            logger.error(f"缓存配置验证失败: {str(e)}")
            return {"valid": False, "errors": [str(e)]}

    def _invalidate_cache(self, cache_types: List[str] = None):
        """使缓存失效"""
        try:
            if cache_types is None:
                cache_types = ["sync_status", "event_stats", "storage_list", "config_validation"]
            
            for cache_type in cache_types:
                cache_key = f"transfersync_{cache_type}"
                
                # 清理系统级缓存
                if self._cache_manager:
                    self._cache_manager.delete(cache_key)
                
                # 清理本地缓存中匹配的键
                keys_to_remove = [key for key in self._local_cache.keys() if cache_key in key]
                for key in keys_to_remove:
                    del self._local_cache[key]
                    
            logger.debug(f"缓存失效完成: {cache_types}")
            
        except Exception as e:
            logger.warning(f"缓存失效失败: {str(e)}")

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
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'notification_channels',
                                            'label': '通知渠道',
                                            'items': self._get_available_notification_channels(),
                                            'multiple': True,
                                            'chips': True,
                                            'clearable': True,
                                            'hint': '选择要发送通知的渠道，留空则发送到所有可用渠道',
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
                                            'hint': '同时处理的文件数量',
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
                                            'label': '批处理大小',
                                            'type': 'number',
                                            'placeholder': '100',
                                            'hint': '每批次处理的文件数量',
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
                                            'hint': '小于此大小的文件将被忽略',
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
                                            'hint': '0表示无限制，大于此大小的文件将被忽略',
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
                                            'placeholder': '每行一个模式，支持通配符和正则表达式\n例如：\n*.tmp\n*.log\n.DS_Store',
                                            'hint': '匹配这些模式的文件将被跳过',
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
                                            'model': 'trigger_events',
                                            'label': '监听事件类型',
                                            'items': [
                                                {'title': '整理完成', 'value': 'transfer.complete'},
                                                {'title': '下载添加', 'value': 'download.added'},
                                                {'title': '订阅完成', 'value': 'subscribe.complete'},
                                                {'title': '媒体添加', 'value': 'media.added'}
                                            ],
                                            'multiple': True,
                                            'chips': True,
                                            'hint': '选择触发同步的事件类型',
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
                                            'label': '显示进度',
                                            'hint': '在处理过程中显示进度信息',
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

    def get_dashboard(self) -> Optional[Dict[str, Any]]:
        """获取仪表板Widget数据"""
        if not self._enabled:
            return None
            
        try:
            # 获取基本统计信息
            total_events = sum(self._event_statistics.values()) if self._event_statistics else 0
            sync_paths_count = len(self._copy_paths)
            
            # 获取最近同步状态
            last_sync_str = "暂无记录"
            if self._last_sync_time:
                time_diff = datetime.now() - self._last_sync_time
                if time_diff.days > 0:
                    last_sync_str = f"{time_diff.days}天前"
                elif time_diff.seconds > 3600:
                    last_sync_str = f"{time_diff.seconds // 3600}小时前"
                elif time_diff.seconds > 60:
                    last_sync_str = f"{time_diff.seconds // 60}分钟前"
                else:
                    last_sync_str = "刚刚"
            
            # 计算同步状态颜色
            status_color = "success" if self._enabled else "warning"
            if self._copy_paths and not self._enabled:
                status_color = "error"
                
            # Widget数据结构
            widget_data = {
                "id": "transfersync_widget",
                "name": "整理后同步",
                "icon": "mdi-sync",
                "color": status_color,
                "subtitle": f"{sync_paths_count} 个同步路径",
                "data": {
                    "enabled": self._enabled,
                    "sync_paths": sync_paths_count,
                    "total_events": total_events,
                    "last_sync": last_sync_str,
                    "sync_strategy": self._sync_strategy.value,
                    "sync_mode": self._sync_mode.value
                },
                "elements": [
                    {
                        "type": "text",
                        "title": "同步路径",
                        "value": f"{sync_paths_count} 个",
                        "icon": "mdi-folder-sync"
                    },
                    {
                        "type": "text", 
                        "title": "处理事件",
                        "value": f"{total_events} 次",
                        "icon": "mdi-counter"
                    },
                    {
                        "type": "text",
                        "title": "上次同步", 
                        "value": last_sync_str,
                        "icon": "mdi-clock-outline"
                    },
                    {
                        "type": "text",
                        "title": "同步策略",
                        "value": self._sync_strategy.value,
                        "icon": "mdi-cog"
                    }
                ],
                "actions": [
                    {
                        "type": "button",
                        "text": "立即同步",
                        "icon": "mdi-sync",
                        "color": "primary",
                        "action": {
                            "type": "api",
                            "url": "/api/v1/plugin/TransferSync/sync_now"
                        }
                    },
                    {
                        "type": "button", 
                        "text": "查看统计",
                        "icon": "mdi-chart-line",
                        "color": "info",
                        "action": {
                            "type": "page",
                            "page": "plugin_detail",
                            "params": {"plugin_id": "TransferSync"}
                        }
                    }
                ]
            }
            
            # 添加最近事件信息（如果有）
            if self._event_statistics:
                recent_events = []
                for event_type, count in sorted(self._event_statistics.items(), 
                                              key=lambda x: x[1], reverse=True)[:3]:
                    recent_events.append({
                        "type": "progress",
                        "title": event_type,
                        "value": count,
                        "max_value": total_events,
                        "percentage": int((count / total_events * 100)) if total_events > 0 else 0,
                        "color": "primary"
                    })
                
                if recent_events:
                    widget_data["elements"].extend(recent_events)
            
            return widget_data
            
        except Exception as e:
            logger.error(f"获取仪表板Widget数据失败: {str(e)}")
            return None

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
            },
            {
                "cmd": "/sync_status",
                "event": EventType.PluginAction,
                "desc": "查看同步状态",
                "category": "文件管理",
                "data": {
                    "action": "sync_status"
                }
            },
            {
                "cmd": "/sync_quick",
                "event": EventType.PluginAction,
                "desc": "快速同步",
                "category": "文件管理",
                "data": {
                    "action": "sync_quick"
                }
            },
            {
                "cmd": "/sync_config",
                "event": EventType.PluginAction,
                "desc": "同步配置管理",
                "category": "文件管理",
                "data": {
                    "action": "sync_config"
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
            },
            {
                "path": "/performance_metrics",
                "endpoint": self.get_performance_metrics,
                "methods": ["GET"],
                "summary": "获取性能监控指标",
                "description": "获取系统资源使用情况和插件性能指标"
            },
            {
                "path": "/health_status",
                "endpoint": self.get_health_status,
                "methods": ["GET"],
                "summary": "健康状态检查",
                "description": "获取插件和相关服务的健康状态"
            },
            {
                "path": "/diagnostics",
                "endpoint": self.get_plugin_diagnostics,
                "methods": ["GET"],
                "summary": "插件诊断信息",
                "description": "获取完整的插件诊断和状态信息"
            },
            {
                "path": "/create_backup",
                "endpoint": self._api_create_backup,
                "methods": ["POST"],
                "summary": "创建配置备份",
                "description": "创建当前插件配置的备份"
            },
            {
                "path": "/restore_backup",
                "endpoint": self._api_restore_backup,
                "methods": ["POST"],
                "summary": "恢复配置备份",
                "description": "从指定备份恢复插件配置"
            },
            {
                "path": "/validate_config",
                "endpoint": self._api_validate_config,
                "methods": ["POST"],
                "summary": "验证配置",
                "description": "验证插件配置并提供建议"
            },
            {
                "path": "/browse_directory",
                "endpoint": self.browse_directory,
                "methods": ["GET"],
                "summary": "浏览目录",
                "description": "浏览指定存储的目录结构"
            },
            {
                "path": "/get_storage_list",
                "endpoint": self.get_storage_list,
                "methods": ["GET"], 
                "summary": "获取存储列表",
                "description": "获取所有可用的存储列表"
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
        """获取插件工作流动作 - 增强版"""
        actions = [
            {
                "id": "sync_single",
                "name": "同步单个文件/目录",
                "description": "同步指定的文件或目录到目标位置",
                "func": self.sync_single_action,
                "kwargs": {},
                "category": "文件操作",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string", 
                            "title": "文件/目录路径",
                            "description": "要同步的文件或目录的完整路径"
                        },
                        "target_override": {
                            "type": "string",
                            "title": "目标路径覆盖",
                            "description": "覆盖默认目标路径（可选）"
                        }
                    },
                    "required": ["path"]
                }
            },
            {
                "id": "sync_incremental",
                "name": "执行增量同步",
                "description": "执行增量同步，只同步变更的文件",
                "func": self.sync_incremental_action,
                "kwargs": {},
                "category": "同步操作",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "force": {
                            "type": "boolean",
                            "title": "强制同步",
                            "description": "即使未检测到变更也执行同步",
                            "default": False
                        }
                    }
                }
            },
            {
                "id": "sync_full",
                "name": "执行全量同步",
                "description": "执行全量同步，同步所有配置的路径",
                "func": self.sync_full_action,
                "kwargs": {},
                "category": "同步操作",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "verify": {
                            "type": "boolean",
                            "title": "验证同步结果",
                            "description": "同步完成后验证文件完整性",
                            "default": True
                        }
                    }
                }
            },
            {
                "id": "sync_conditional",
                "name": "条件同步",
                "description": "根据条件决定是否执行同步操作",
                "func": self.sync_conditional_action,
                "kwargs": {},
                "category": "条件操作",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "condition_type": {
                            "type": "string",
                            "title": "条件类型",
                            "enum": ["file_size", "file_age", "disk_space", "time_window"],
                            "description": "触发同步的条件类型"
                        },
                        "condition_value": {
                            "type": "string",
                            "title": "条件值",
                            "description": "条件的具体值"
                        },
                        "sync_type": {
                            "type": "string",
                            "title": "同步类型",
                            "enum": ["incremental", "full", "single"],
                            "default": "incremental"
                        }
                    },
                    "required": ["condition_type", "condition_value"]
                }
            },
            {
                "id": "sync_with_notification",
                "name": "同步并通知",
                "description": "执行同步操作并发送结果通知",
                "func": self.sync_with_notification_action,
                "kwargs": {},
                "category": "通知操作",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "sync_type": {
                            "type": "string",
                            "title": "同步类型",
                            "enum": ["incremental", "full", "single"],
                            "default": "incremental"
                        },
                        "notify_success": {
                            "type": "boolean",
                            "title": "成功时通知",
                            "default": True
                        },
                        "notify_failure": {
                            "type": "boolean", 
                            "title": "失败时通知",
                            "default": True
                        },
                        "custom_message": {
                            "type": "string",
                            "title": "自定义消息",
                            "description": "自定义通知消息（可选）"
                        }
                    }
                }
            },
            {
                "id": "sync_cleanup",
                "name": "同步清理",
                "description": "清理同步过程中的临时文件和过期数据",
                "func": self.sync_cleanup_action,
                "kwargs": {},
                "category": "维护操作",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "cleanup_temp": {
                            "type": "boolean",
                            "title": "清理临时文件",
                            "default": True
                        },
                        "cleanup_logs": {
                            "type": "boolean",
                            "title": "清理旧日志",
                            "default": False
                        },
                        "days_to_keep": {
                            "type": "integer",
                            "title": "保留天数",
                            "default": 7,
                            "minimum": 1
                        }
                    }
                }
            },
            {
                "id": "sync_status_check",
                "name": "同步状态检查",
                "description": "检查同步状态并返回详细信息",
                "func": self.sync_status_check_action,
                "kwargs": {},
                "category": "监控操作",
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "detailed": {
                            "type": "boolean",
                            "title": "详细信息",
                            "description": "返回详细的状态信息",
                            "default": False
                        }
                    }
                }
            }
        ]
        
        return actions

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
        """获取事件统计信息 - 使用缓存优化"""
        try:
            # 首先尝试从缓存获取
            cached_stats = self._get_cached_event_statistics()
            
            # 如果有原始统计数据，进行成功率计算
            if hasattr(self, '_event_statistics') and self._event_statistics:
                for event_key, stats in self._event_statistics.items():
                    if isinstance(stats, dict):
                        total_count = stats.get('total_count', 0)
                        if total_count > 0:
                            success_count = stats.get('success_count', 0)
                            stats['success_rate'] = round((success_count / total_count) * 100, 2)
                        else:
                            stats['success_rate'] = 0
                        
                        # 添加最后触发时间
                        stats['last_triggered'] = stats.get('last_triggered', '从未触发')
                
                # 使缓存失效，确保下次获取最新数据
                self._invalidate_cache(["event_stats"])
                return self._event_statistics
            else:
                # 返回缓存的统计信息
                return cached_stats.get('statistics', {})
                
        except Exception as e:
            logger.error(f"获取事件统计信息失败: {str(e)}")
            return {}

    # === 新增的工作流动作方法 ===
    def sync_conditional_action(self, action_content) -> Tuple[bool, Any]:
        """工作流动作：条件同步"""
        try:
            condition_type = action_content.get("condition_type")
            condition_value = action_content.get("condition_value") 
            sync_type = action_content.get("sync_type", "incremental")
            
            # 检查条件是否满足
            condition_met = self._check_sync_condition(condition_type, condition_value)
            
            if not condition_met:
                return True, f"条件不满足，跳过同步：{condition_type}={condition_value}"
            
            # 执行相应的同步操作
            if sync_type == "incremental":
                return self.sync_incremental_action({"force": False})
            elif sync_type == "full":
                return self.sync_full_action({"verify": True})
            elif sync_type == "single":
                path = action_content.get("path", "")
                return self.sync_single_action(path)
            else:
                return False, f"未知的同步类型: {sync_type}"
                
        except Exception as e:
            logger.error(f"条件同步失败: {str(e)}")
            return False, str(e)

    def sync_with_notification_action(self, action_content) -> Tuple[bool, Any]:
        """工作流动作：同步并通知"""
        try:
            sync_type = action_content.get("sync_type", "incremental")
            notify_success = action_content.get("notify_success", True)
            notify_failure = action_content.get("notify_failure", True)
            custom_message = action_content.get("custom_message", "")
            
            # 执行同步操作
            if sync_type == "incremental":
                success, result = self.sync_incremental_action(action_content)
            elif sync_type == "full":
                success, result = self.sync_full_action(action_content)
            elif sync_type == "single":
                success, result = self.sync_single_action(action_content)
            else:
                return False, f"未知的同步类型: {sync_type}"
            
            # 发送通知
            if success and notify_success:
                title = "🎉 同步成功"
                message = custom_message or f"{sync_type}同步操作已成功完成"
                self._send_notification(title, f"{message}\n\n结果: {result}")
            elif not success and notify_failure:
                title = "❌ 同步失败"
                message = custom_message or f"{sync_type}同步操作失败"
                self._send_notification(title, f"{message}\n\n错误: {result}")
            
            return success, result
            
        except Exception as e:
            logger.error(f"同步并通知失败: {str(e)}")
            return False, str(e)

    def sync_cleanup_action(self, action_content) -> Tuple[bool, Any]:
        """工作流动作：同步清理"""
        try:
            cleanup_temp = action_content.get("cleanup_temp", True)
            cleanup_logs = action_content.get("cleanup_logs", False)
            days_to_keep = action_content.get("days_to_keep", 7)
            
            cleaned_items = []
            
            # 清理临时文件
            if cleanup_temp:
                temp_count = self._cleanup_temp_files()
                cleaned_items.append(f"临时文件: {temp_count} 个")
            
            # 清理旧日志
            if cleanup_logs:
                log_count = self._cleanup_old_logs(days_to_keep)
                cleaned_items.append(f"旧日志: {log_count} 个")
            
            # 清理缓存
            self._invalidate_cache()
            cleaned_items.append("插件缓存已清理")
            
            result = f"清理完成 - {', '.join(cleaned_items)}"
            return True, result
            
        except Exception as e:
            logger.error(f"同步清理失败: {str(e)}")
            return False, str(e)

    def sync_status_check_action(self, action_content) -> Tuple[bool, Any]:
        """工作流动作：同步状态检查"""
        try:
            detailed = action_content.get("detailed", False)
            
            if detailed:
                # 返回详细状态信息
                status = self._get_cached_sync_status()
                stats = self._get_cached_event_statistics()
                
                result = {
                    "basic_status": status,
                    "event_statistics": stats,
                    "cache_status": {
                        "local_cache_size": len(self._local_cache),
                        "system_cache_available": self._cache_manager is not None
                    },
                    "sync_paths_status": self._check_paths_status()
                }
            else:
                # 返回简单状态信息
                result = {
                    "enabled": self._enabled,
                    "sync_paths": len(self._copy_paths),
                    "last_sync": self._last_sync_time.isoformat() if self._last_sync_time else None,
                    "total_events": sum(self._event_statistics.values()) if self._event_statistics else 0
                }
            
            return True, result
            
        except Exception as e:
            logger.error(f"状态检查失败: {str(e)}")
            return False, str(e)

    def _check_sync_condition(self, condition_type: str, condition_value: str) -> bool:
        """检查同步条件是否满足"""
        try:
            if condition_type == "file_size":
                # 检查文件大小条件
                min_size = int(condition_value)  # MB
                total_size = self._calculate_total_file_size()
                return total_size >= min_size
                
            elif condition_type == "file_age":
                # 检查文件年龄条件
                max_hours = int(condition_value)
                return self._check_file_age_condition(max_hours)
                
            elif condition_type == "disk_space":
                # 检查磁盘空间条件
                min_free_gb = int(condition_value)
                return self._check_disk_space_condition(min_free_gb)
                
            elif condition_type == "time_window":
                # 检查时间窗口条件
                return self._check_time_window_condition(condition_value)
                
            return False
            
        except Exception as e:
            logger.error(f"检查同步条件失败: {str(e)}")
            return False

    def _cleanup_temp_files(self) -> int:
        """清理临时文件"""
        try:
            count = 0
            # 这里可以添加具体的临时文件清理逻辑
            logger.debug(f"清理了 {count} 个临时文件")
            return count
        except Exception as e:
            logger.error(f"清理临时文件失败: {str(e)}")
            return 0

    def _cleanup_old_logs(self, days_to_keep: int) -> int:
        """清理旧日志文件"""
        try:
            count = 0
            # 这里可以添加具体的日志清理逻辑
            logger.debug(f"清理了 {days_to_keep} 天前的 {count} 个日志文件")
            return count
        except Exception as e:
            logger.error(f"清理旧日志失败: {str(e)}")
            return 0

    def _check_paths_status(self) -> Dict[str, Any]:
        """检查同步路径状态"""
        try:
            path_status = {
                "total_paths": len(self._copy_paths),
                "accessible_paths": 0,
                "inaccessible_paths": []
            }
            
            for path_config in self._copy_paths:
                source = path_config.get('source_path', '')
                target = path_config.get('target_path', '')
                
                if Path(source).exists() and Path(target).parent.exists():
                    path_status["accessible_paths"] += 1
                else:
                    path_status["inaccessible_paths"].append({
                        "source": source,
                        "target": target,
                        "issue": "路径不可访问"
                    })
            
            return path_status
            
        except Exception as e:
            logger.error(f"检查路径状态失败: {str(e)}")
            return {"total_paths": 0, "accessible_paths": 0, "inaccessible_paths": []}

    def _calculate_total_file_size(self) -> int:
        """计算总文件大小 (MB)"""
        try:
            total_size = 0
            for path_config in self._copy_paths:
                source = Path(path_config.get('source_path', ''))
                if source.exists():
                    if source.is_file():
                        total_size += source.stat().st_size
                    elif source.is_dir():
                        total_size += sum(f.stat().st_size for f in source.rglob('*') if f.is_file())
            
            return total_size // (1024 * 1024)  # 转换为MB
            
        except Exception as e:
            logger.error(f"计算文件大小失败: {str(e)}")
            return 0

    def _check_file_age_condition(self, max_hours: int) -> bool:
        """检查文件年龄条件"""
        try:
            cutoff_time = datetime.now() - timedelta(hours=max_hours)
            
            for path_config in self._copy_paths:
                source = Path(path_config.get('source_path', ''))
                if source.exists():
                    if source.is_file():
                        if datetime.fromtimestamp(source.stat().st_mtime) > cutoff_time:
                            return True
                    elif source.is_dir():
                        for file_path in source.rglob('*'):
                            if file_path.is_file() and datetime.fromtimestamp(file_path.stat().st_mtime) > cutoff_time:
                                return True
            
            return False
            
        except Exception as e:
            logger.error(f"检查文件年龄条件失败: {str(e)}")
            return False

    def _check_disk_space_condition(self, min_free_gb: int) -> bool:
        """检查磁盘空间条件"""
        try:
            import shutil
            for path_config in self._copy_paths:
                target = path_config.get('target_path', '')
                if target:
                    free_space = shutil.disk_usage(Path(target).parent).free
                    free_gb = free_space // (1024 ** 3)
                    if free_gb < min_free_gb:
                        return False
            return True
            
        except Exception as e:
            logger.error(f"检查磁盘空间条件失败: {str(e)}")
            return False

    def _check_time_window_condition(self, time_window: str) -> bool:
        """检查时间窗口条件"""
        try:
            # 解析时间窗口格式，如 "09:00-18:00"
            if '-' not in time_window:
                return True
                
            start_time_str, end_time_str = time_window.split('-')
            current_time = datetime.now().time()
            
            start_time = datetime.strptime(start_time_str.strip(), '%H:%M').time()
            end_time = datetime.strptime(end_time_str.strip(), '%H:%M').time()
            
            if start_time <= end_time:
                return start_time <= current_time <= end_time
            else:
                # 跨日情况
                return current_time >= start_time or current_time <= end_time
                
        except Exception as e:
            logger.error(f"检查时间窗口条件失败: {str(e)}")
            return True

    # === 高级扩展功能 ===
    def get_performance_metrics(self) -> Dict[str, Any]:
        """获取性能监控指标"""
        try:
            import psutil
            import threading
            
            # 系统资源使用情况
            cpu_percent = psutil.cpu_percent(interval=1)
            memory_info = psutil.virtual_memory()
            disk_usage = psutil.disk_usage('/')
            
            # 插件特定指标
            self._performance_metrics.update({
                "timestamp": datetime.now().isoformat(),
                "system_resources": {
                    "cpu_percent": cpu_percent,
                    "memory_percent": memory_info.percent,
                    "memory_available_gb": round(memory_info.available / (1024**3), 2),
                    "disk_free_gb": round(disk_usage.free / (1024**3), 2)
                },
                "plugin_metrics": {
                    "enabled": self._enabled,
                    "sync_paths_count": len(self._copy_paths),
                    "active_threads": threading.active_count(),
                    "cache_size": len(self._local_cache),
                    "event_count": sum(self._event_statistics.values()) if self._event_statistics else 0
                },
                "sync_performance": {
                    "last_sync_time": self._last_sync_time.isoformat() if self._last_sync_time else None,
                    "avg_sync_duration": self._calculate_avg_sync_duration(),
                    "success_rate": self._calculate_success_rate()
                }
            })
            
            return self._performance_metrics
            
        except Exception as e:
            logger.error(f"获取性能指标失败: {str(e)}")
            return {"error": str(e)}

    def get_health_status(self) -> Dict[str, Any]:
        """获取插件健康状态"""
        try:
            checks = {}
            overall_status = "healthy"
            
            # 检查基本配置
            if not self._copy_paths:
                checks["sync_paths"] = {"status": "error", "message": "未配置同步路径"}
                overall_status = "unhealthy"
            else:
                checks["sync_paths"] = {"status": "ok", "message": f"已配置 {len(self._copy_paths)} 个同步路径"}
            
            # 检查路径可访问性
            accessible_count = 0
            total_paths = len(self._copy_paths)
            for path_config in self._copy_paths:
                if Path(path_config.get('source_path', '')).exists():
                    accessible_count += 1
            
            if accessible_count == total_paths:
                checks["path_accessibility"] = {"status": "ok", "message": "所有路径可访问"}
            elif accessible_count > 0:
                checks["path_accessibility"] = {"status": "warning", "message": f"{accessible_count}/{total_paths} 路径可访问"}
                if overall_status == "healthy":
                    overall_status = "degraded"
            else:
                checks["path_accessibility"] = {"status": "error", "message": "无路径可访问"}
                overall_status = "unhealthy"
            
            # 检查服务状态
            checks["notification_service"] = {
                "status": "ok" if self._notification_helper else "error",
                "message": "通知服务正常" if self._notification_helper else "通知服务不可用"
            }
            
            checks["storage_service"] = {
                "status": "ok" if self._storage_helper else "error", 
                "message": "存储服务正常" if self._storage_helper else "存储服务不可用"
            }
            
            # 检查缓存状态
            checks["cache_service"] = {
                "status": "ok" if self._cache_manager else "warning",
                "message": "系统缓存可用" if self._cache_manager else "仅本地缓存可用"
            }
            
            self._health_status = {
                "status": overall_status,
                "timestamp": datetime.now().isoformat(),
                "checks": checks,
                "uptime": self._get_plugin_uptime()
            }
            
            return self._health_status
            
        except Exception as e:
            logger.error(f"健康检查失败: {str(e)}")
            return {
                "status": "error",
                "timestamp": datetime.now().isoformat(),
                "error": str(e)
            }

    def create_backup(self) -> Tuple[bool, str]:
        """创建配置备份"""
        try:
            backup_data = {
                "version": self.plugin_version,
                "timestamp": datetime.now().isoformat(),
                "config": {
                    "enabled": self._enabled,
                    "copy_paths": self._copy_paths,
                    "sync_strategy": self._sync_strategy.value,
                    "sync_mode": self._sync_mode.value,
                    "notification_settings": {
                        "enabled": self._enable_notifications,
                        "channels": self._notification_channels
                    },
                    "advanced_settings": {
                        "max_workers": self._max_workers,
                        "batch_size": self._batch_size,
                        "file_filters": self._file_filters,
                        "exclude_patterns": self._exclude_patterns
                    },
                    "schedule_settings": {
                        "enable_incremental": self._enable_incremental,
                        "incremental_cron": self._incremental_cron,
                        "enable_full_sync": self._enable_full_sync,
                        "full_sync_cron": self._full_sync_cron
                    },
                    "trigger_events": [event.value for event in self._trigger_events]
                },
                "statistics": self._event_statistics,
                "performance_metrics": self._performance_metrics
            }
            
            # 使用系统缓存存储备份
            backup_key = f"transfersync_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            if self._cache_manager:
                self._cache_manager.set(backup_key, backup_data, ttl=86400 * 30)  # 保存30天
                
            return True, f"备份已创建: {backup_key}"
            
        except Exception as e:
            logger.error(f"创建备份失败: {str(e)}")
            return False, str(e)

    def restore_backup(self, backup_key: str) -> Tuple[bool, str]:
        """从备份恢复配置"""
        try:
            if not self._cache_manager:
                return False, "缓存管理器不可用"
                
            backup_data = self._cache_manager.get(backup_key)
            if not backup_data:
                return False, f"找不到备份: {backup_key}"
            
            # 验证备份数据
            if not isinstance(backup_data, dict) or 'config' not in backup_data:
                return False, "备份数据格式无效"
            
            config = backup_data['config']
            
            # 恢复配置
            self._enabled = config.get('enabled', False)
            self._copy_paths = config.get('copy_paths', [])
            self._sync_strategy = SyncStrategy(config.get('sync_strategy', 'copy'))
            self._sync_mode = SyncMode(config.get('sync_mode', 'immediate'))
            
            # 恢复通知设置
            notification_settings = config.get('notification_settings', {})
            self._enable_notifications = notification_settings.get('enabled', False)
            self._notification_channels = notification_settings.get('channels', [])
            
            # 恢复高级设置
            advanced_settings = config.get('advanced_settings', {})
            self._max_workers = advanced_settings.get('max_workers', 4)
            self._batch_size = advanced_settings.get('batch_size', 100)
            self._file_filters = advanced_settings.get('file_filters', [])
            self._exclude_patterns = advanced_settings.get('exclude_patterns', [])
            
            # 恢复调度设置
            schedule_settings = config.get('schedule_settings', {})
            self._enable_incremental = schedule_settings.get('enable_incremental', False)
            self._incremental_cron = schedule_settings.get('incremental_cron', '0 */6 * * *')
            self._enable_full_sync = schedule_settings.get('enable_full_sync', False)
            self._full_sync_cron = schedule_settings.get('full_sync_cron', '0 2 * * 0')
            
            # 恢复触发事件
            trigger_events = config.get('trigger_events', ['transfer.complete'])
            self._trigger_events = [TriggerEvent(event) for event in trigger_events]
            
            # 重新初始化
            self._register_event_listeners()
            self._setup_scheduler()
            
            # 清理缓存
            self._invalidate_cache()
            
            return True, f"配置已从备份恢复: {backup_key}"
            
        except Exception as e:
            logger.error(f"恢复备份失败: {str(e)}")
            return False, str(e)

    def validate_configuration(self, config: dict) -> Dict[str, Any]:
        """高级配置验证"""
        try:
            validation_result = self._cache_config_validation(config)
            
            # 添加额外的高级验证
            warnings = []
            errors = validation_result.get('errors', [])
            
            # 检查性能配置
            max_workers = config.get('max_workers', 4)
            if max_workers > 10:
                warnings.append("工作线程数过高可能影响系统性能")
            
            batch_size = config.get('batch_size', 100)
            if batch_size > 1000:
                warnings.append("批处理大小过大可能消耗过多内存")
            
            # 检查路径配置
            copy_paths = config.get('copy_paths', [])
            for path_config in copy_paths:
                source = path_config.get('source_path', '')
                target = path_config.get('target_path', '')
                
                if source == target:
                    errors.append(f"源路径和目标路径相同: {source}")
                
                if source and target and Path(source).is_relative_to(Path(target)):
                    warnings.append(f"源路径是目标路径的子目录: {source} -> {target}")
            
            # 检查Cron表达式
            for cron_key in ['incremental_cron', 'full_sync_cron']:
                cron_expr = config.get(cron_key)
                if cron_expr and not self._validate_cron_expression(cron_expr):
                    errors.append(f"无效的Cron表达式: {cron_key}={cron_expr}")
            
            validation_result.update({
                'warnings': warnings,
                'valid': len(errors) == 0,
                'performance_impact': self._assess_performance_impact(config),
                'recommendations': self._generate_recommendations(config, warnings, errors)
            })
            
            return validation_result
            
        except Exception as e:
            logger.error(f"配置验证失败: {str(e)}")
            return {
                'valid': False,
                'errors': [str(e)],
                'warnings': [],
                'recommendations': []
            }

    def get_plugin_diagnostics(self) -> Dict[str, Any]:
        """获取插件诊断信息"""
        try:
            diagnostics = {
                "basic_info": {
                    "version": self.plugin_version,
                    "enabled": self._enabled,
                    "uptime": self._get_plugin_uptime(),
                    "last_sync": self._last_sync_time.isoformat() if self._last_sync_time else None
                },
                "configuration": {
                    "sync_paths_count": len(self._copy_paths),
                    "sync_strategy": self._sync_strategy.value,
                    "sync_mode": self._sync_mode.value,
                    "notifications_enabled": self._enable_notifications,
                    "incremental_enabled": self._enable_incremental,
                    "full_sync_enabled": self._enable_full_sync
                },
                "performance": self.get_performance_metrics(),
                "health": self.get_health_status(),
                "statistics": {
                    "event_counts": self._event_statistics,
                    "cache_status": {
                        "local_cache_size": len(self._local_cache),
                        "system_cache_available": self._cache_manager is not None
                    }
                },
                "system_integration": {
                    "notification_helper": self._notification_helper is not None,
                    "storage_helper": self._storage_helper is not None,
                    "mediaserver_helper": self._mediaserver_helper is not None,
                    "scheduler": self._scheduler is not None
                }
            }
            
            return diagnostics
            
        except Exception as e:
            logger.error(f"获取诊断信息失败: {str(e)}")
            return {"error": str(e)}

    def _calculate_avg_sync_duration(self) -> float:
        """计算平均同步耗时"""
        try:
            # 这里可以从历史记录计算，暂时返回模拟值
            return 0.0
        except:
            return 0.0

    def _calculate_success_rate(self) -> float:
        """计算同步成功率"""
        try:
            if not self._event_statistics:
                return 100.0
            
            total_events = sum(self._event_statistics.values())
            if total_events == 0:
                return 100.0
            
            # 这里需要更详细的统计信息来计算实际成功率
            # 暂时基于事件统计返回估算值
            return min(100.0, (total_events * 0.95))  # 假设95%成功率
            
        except:
            return 0.0

    def _get_plugin_uptime(self) -> str:
        """获取插件运行时间"""
        try:
            # 这里应该记录插件启动时间，暂时返回当前会话时间
            return "会话时间未记录"
        except:
            return "未知"

    def _validate_cron_expression(self, cron_expr: str) -> bool:
        """验证Cron表达式"""
        try:
            from apscheduler.triggers.cron import CronTrigger
            CronTrigger.from_crontab(cron_expr)
            return True
        except:
            return False

    def _assess_performance_impact(self, config: dict) -> str:
        """评估配置的性能影响"""
        try:
            impact_score = 0
            
            # 工作线程数影响
            max_workers = config.get('max_workers', 4)
            if max_workers > 8:
                impact_score += 2
            elif max_workers > 4:
                impact_score += 1
            
            # 批处理大小影响
            batch_size = config.get('batch_size', 100)
            if batch_size > 500:
                impact_score += 2
            elif batch_size > 200:
                impact_score += 1
            
            # 同步路径数量影响
            paths_count = len(config.get('copy_paths', []))
            if paths_count > 10:
                impact_score += 2
            elif paths_count > 5:
                impact_score += 1
            
            if impact_score >= 4:
                return "高"
            elif impact_score >= 2:
                return "中"
            else:
                return "低"
                
        except:
            return "未知"

    def _generate_recommendations(self, config: dict, warnings: List[str], errors: List[str]) -> List[str]:
        """生成配置建议"""
        recommendations = []
        
        try:
            if errors:
                recommendations.append("请先修复配置错误再启用插件")
            
            if warnings:
                recommendations.append("建议关注配置警告以优化性能")
            
            max_workers = config.get('max_workers', 4)
            if max_workers > 8:
                recommendations.append("建议将工作线程数设置为4-8之间以平衡性能和资源消耗")
            
            paths_count = len(config.get('copy_paths', []))
            if paths_count > 15:
                recommendations.append("同步路径较多，建议分批配置或使用更高效的同步策略")
            
            if not config.get('enable_notifications', False):
                recommendations.append("建议启用通知功能以及时了解同步状态")
            
            return recommendations
            
        except:
            return ["配置分析失败"]

    # === API包装方法 ===
    def _api_create_backup(self) -> dict:
        """API接口：创建备份"""
        try:
            success, message = self.create_backup()
            return {
                "success": success,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def _api_restore_backup(self, backup_key: str = None) -> dict:
        """API接口：恢复备份"""
        try:
            if not backup_key:
                return {
                    "success": False,
                    "message": "缺少备份键参数"
                }
                
            success, message = self.restore_backup(backup_key)
            return {
                "success": success,
                "message": message,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {
                "success": False,
                "message": str(e),
                "timestamp": datetime.now().isoformat()
            }

    def _api_validate_config(self, config: dict = None) -> dict:
        """API接口：验证配置"""
        try:
            if not config:
                # 如果没有提供配置，验证当前配置
                config = {
                    "enabled": self._enabled,
                    "copy_paths": self._copy_paths,
                    "sync_strategy": self._sync_strategy.value,
                    "sync_mode": self._sync_mode.value,
                    "max_workers": self._max_workers,
                    "batch_size": self._batch_size,
                    "enable_notifications": self._enable_notifications,
                    "notification_channels": self._notification_channels,
                    "incremental_cron": self._incremental_cron,
                    "full_sync_cron": self._full_sync_cron
                }
                
            validation_result = self.validate_configuration(config)
            validation_result["timestamp"] = datetime.now().isoformat()
            
            return validation_result
            
        except Exception as e:
            return {
                "valid": False,
                "errors": [str(e)],
                "warnings": [],
                "recommendations": [],
                "timestamp": datetime.now().isoformat()
            }

    def _send_notification(self, title: str, text: str, image: str = None):
        """发送通知 - 使用V2的NotificationHelper方式"""
        if not self._enable_notifications:
            return

        try:
            # 获取要发送的渠道
            target_channels = []
            if self._notification_channels:
                # 用户指定的渠道
                specified_channels = self._notification_channels if isinstance(self._notification_channels, list) else [self._notification_channels]
                for channel_name in specified_channels:
                    if channel_name in self._available_channels:
                        service_info = self._notification_helper.get_service(name=channel_name)
                        if service_info and service_info.instance:
                            target_channels.append(service_info)
            else:
                # 获取所有可用的通知服务
                all_services = self._notification_helper.get_services()
                target_channels = list(all_services.values())
            
            # 发送通知到选定的渠道
            success_count = 0
            for service_info in target_channels:
                try:
                    if service_info.instance and hasattr(service_info.instance, 'send_message'):
                        service_info.instance.send_message(
                            title=title,
                            text=text,
                            image=image
                        )
                        success_count += 1
                        logger.debug(f"通知已发送到 {service_info.name}")
                except Exception as e:
                    logger.error(f"发送通知到 {service_info.name} 失败: {str(e)}")
            
            if success_count > 0:
                logger.info(f"通知发送完成，成功 {success_count} 个渠道")
            else:
                logger.warning("没有可用的通知渠道")
                
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}")

    def _get_available_notification_channels(self) -> List[Dict[str, str]]:
        """获取可用的通知渠道选项"""
        try:
            channels = []
            all_services = self._notification_helper.get_services()
            for name, service_info in all_services.items():
                channels.append({
                    "title": f"{name} ({service_info.type})" if service_info.type else name,
                    "value": name
                })
            return channels
        except Exception as e:
            logger.error(f"获取通知渠道失败: {str(e)}")
            return []

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
    
    # === 存储API相关方法 ===
    def get_storage_list(self) -> dict:
        """API接口：获取存储列表"""
        try:
            storages = self._storage_helper.get_storages()
            storage_list = []
            for storage in storages:
                storage_list.append({
                    "name": storage.name,
                    "type": storage.type,
                    "enabled": storage.enabled,
                    "path": storage.config.get("path", "") if storage.config else ""
                })
            
            return {
                "code": 0,
                "data": storage_list
            }
        except Exception as e:
            logger.error(f"获取存储列表失败: {str(e)}")
            return {
                "code": 1,
                "message": f"获取存储列表失败: {str(e)}"
            }
    
    def browse_directory(self, storage: str = None, path: str = "/") -> dict:
        """API接口：浏览目录"""
        try:
            from pathlib import Path
            from app.schemas import FileItem
            
            # 如果指定了存储，使用存储API
            if storage:
                # 使用存储API浏览
                storage_obj = self._storage_helper.get_storage(storage)
                if not storage_obj:
                    return {
                        "code": 1,
                        "message": f"存储 {storage} 不存在"
                    }
                
                # 通过存储API浏览目录（这里需要实际的存储API实现）
                items = []
            else:
                # 浏览本地目录
                target_path = Path(path)
                items = []
                
                if target_path.exists() and target_path.is_dir():
                    try:
                        for item in target_path.iterdir():
                            try:
                                is_dir = item.is_dir()
                                file_info = {
                                    "name": item.name,
                                    "path": str(item),
                                    "type": "dir" if is_dir else "file",
                                    "size": 0 if is_dir else item.stat().st_size,
                                    "modified": item.stat().st_mtime
                                }
                                items.append(file_info)
                            except (PermissionError, OSError):
                                continue
                        
                        # 按类型和名称排序（目录优先）
                        items.sort(key=lambda x: (x["type"] != "dir", x["name"].lower()))
                        
                    except PermissionError:
                        return {
                            "code": 1,
                            "message": f"没有权限访问目录: {path}"
                        }
                else:
                    return {
                        "code": 1,
                        "message": f"目录不存在: {path}"
                    }
            
            # 添加父目录导航（如果不是根目录）
            result_items = []
            if path != "/" and Path(path).parent != Path(path):
                result_items.append({
                    "name": "..",
                    "path": str(Path(path).parent),
                    "type": "dir",
                    "size": 0,
                    "modified": 0
                })
            
            result_items.extend(items)
            
            return {
                "code": 0,
                "data": {
                    "current_path": path,
                    "items": result_items[:100]  # 限制返回100个项目
                }
            }
            
        except Exception as e:
            logger.error(f"浏览目录失败: {str(e)}")
            return {
                "code": 1,
                "message": f"浏览目录失败: {str(e)}"
            }