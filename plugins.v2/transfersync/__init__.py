"""
整理后同步插件 - 重构版本
参考 p115strmhelper 的模块化设计，将功能拆分到不同模块
"""
import threading
from typing import Any, Dict, List, Optional

from app.plugins import _PluginBase
from app.log import logger
from app.core.cache import cached

# 导入核心模块
try:
    from .core.config_manager import ConfigManager
    from .core.event_manager import EventManager
    from .core.service_manager import ServiceManager
except ImportError:
    # 如果模块导入失败，使用简化版本
    logger.warning("核心模块导入失败，使用兼容模式")
    ConfigManager = None
    EventManager = None
    ServiceManager = None

# 导入功能模块
from .sync_types import SyncStrategy, SyncMode, TriggerEvent
from .sync_operations import SyncOperations
from .command_handler import CommandHandler


class TransferSync(_PluginBase):
    # 插件名称
    plugin_name = "整理后同步"
    # 插件描述
    plugin_desc = "监听可选择的多种事件类型，根据过滤条件自动同步文件到指定位置，支持多种同步策略、事件统计监控、增量和全量同步。"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/jxxghp/MoviePilot-Plugins/main/icons/sync.png"
    # 插件版本
    plugin_version = "1.5"
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

    def __init__(self):
        super().__init__()
        # 初始化管理器
        if ConfigManager is not None:
            self._config_manager = ConfigManager()
            self._event_manager = EventManager(self)
            self._service_manager = ServiceManager()
            self._use_advanced_features = True
        else:
            # 兼容模式：使用简化功能
            self._config_manager = None
            self._event_manager = None
            self._service_manager = None
            self._use_advanced_features = False
            logger.info("TransferSync 运行在兼容模式下")

        # 插件状态
        self._enabled = False
        self._lock = threading.Lock()

        # 配置属性 - 从配置管理器解析后设置
        self._sync_paths = []  # 多路径支持
        self._delay_minutes = 5
        self._enable_immediate_execution = True
        self._enable_incremental = False
        self._incremental_cron = "0 */6 * * *"
        self._enable_full_sync = False
        self._full_sync_cron = "0 2 * * 0"
        self._enable_notifications = False
        self._notification_channels = []
        self._sync_strategy = SyncStrategy.COPY
        self._sync_mode = SyncMode.IMMEDIATE
        self._max_depth = -1
        self._file_filters = []
        self._exclude_patterns = []
        self._max_file_size = 0
        self._min_file_size = 0
        self._enable_progress = True
        self._max_workers = 4
        self._batch_size = 100
        self._trigger_events = [TriggerEvent.TRANSFER_COMPLETE]
        self._event_conditions = {}

        # 初始化功能模块
        self._sync_ops = None
        self._command_handler = None

    def init_plugin(self, config: dict = None):
        """初始化插件"""
        try:
            if config:
                if self._use_advanced_features and self._config_manager:
                    # 使用配置管理器解析配置
                    parsed_config = self._config_manager.parse_config(config)
                    self._apply_config(parsed_config)
                else:
                    # 兼容模式：直接解析配置
                    self._apply_config_compatible(config)

            # 初始化功能模块
            self._sync_ops = SyncOperations(self)

            # 注册事件监听器
            if self._enabled:
                if self._use_advanced_features and self._event_manager:
                    self._event_manager.register_event_listeners(self._trigger_events)
                    logger.info("TransferSync插件已启用，事件监听器已注册")
                else:
                    # 兼容模式：使用简化的事件注册
                    self._register_event_listeners_compatible()
                    logger.info("TransferSync插件已启用（兼容模式），事件监听器已注册")

            logger.info("TransferSync插件初始化完成")

        except Exception as e:
            logger.error(f"TransferSync插件初始化失败: {str(e)}")

    def _apply_config(self, config: Dict[str, Any]):
        """应用解析后的配置"""
        self._enabled = config.get("enabled", False)
        self._sync_paths = config.get("sync_paths", [])
        self._delay_minutes = config.get("delay_minutes", 5)
        self._enable_immediate_execution = config.get("enable_immediate_execution", True)
        self._enable_incremental = config.get("enable_incremental", False)
        self._incremental_cron = config.get("incremental_cron", "0 */6 * * *")
        self._enable_full_sync = config.get("enable_full_sync", False)
        self._full_sync_cron = config.get("full_sync_cron", "0 2 * * 0")
        self._enable_notifications = config.get("enable_notifications", False)
        self._notification_channels = config.get("notification_channels", [])
        self._sync_strategy = config.get("sync_strategy", SyncStrategy.COPY)
        self._sync_mode = config.get("sync_mode", SyncMode.IMMEDIATE)
        self._max_depth = config.get("max_depth", -1)
        self._file_filters = config.get("file_filters", [])
        self._exclude_patterns = config.get("exclude_patterns", [])
        self._max_file_size = config.get("max_file_size", 0)
        self._min_file_size = config.get("min_file_size", 0)
        self._enable_progress = config.get("enable_progress", True)
        self._max_workers = config.get("max_workers", 4)
        self._batch_size = config.get("batch_size", 100)
        self._trigger_events = config.get("trigger_events", [TriggerEvent.TRANSFER_COMPLETE])
        self._event_conditions = config.get("event_conditions", {})

    @property
    def command_handler(self):
        """延迟初始化命令处理器"""
        if self._command_handler is None:
            self._command_handler = CommandHandler(self)
        return self._command_handler

    def get_state(self) -> bool:
        """获取插件状态"""
        return self._enabled

    def stop_service(self):
        """停止服务"""
        try:
            # 取消事件监听
            self._event_manager.unregister_event_listeners()

            # 清理服务管理器
            self._service_manager.shutdown()

            logger.info("TransferSync服务已停止")
        except Exception as e:
            logger.error(f"停止TransferSync服务失败: {str(e)}")

    # 配置表单相关方法
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """获取插件命令"""
        return [
            {
                "action": "manual_sync",
                "name": "手动同步",
                "icon": "mdi-sync"
            },
            {
                "action": "sync_status",
                "name": "同步状态",
                "icon": "mdi-information"
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """获取插件API"""
        return [
            {
                "path": "/sync_status",
                "endpoint": self.sync_status,
                "methods": ["GET"],
                "summary": "获取同步状态"
            },
            {
                "path": "/manual_sync",
                "endpoint": self.manual_sync,
                "methods": ["POST"],
                "summary": "手动触发同步"
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取配置表单"""
        return [
            # 基础配置
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
                                            'hint': '开启后将监听事件并自动同步'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 6},
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable_immediate_execution',
                                            'label': '启用立即执行',
                                            'hint': '开启后将立即执行同步，否则根据延迟时间执行'
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
                                            'model': 'sync_paths',
                                            'label': '同步路径配置',
                                            'placeholder': '源路径1->目标路径1,源路径2->目标路径2\n或者\n源路径1::目标路径1,源路径2::目标路径2',
                                            'hint': '支持多组同步路径配置，每行一组或用逗号分隔',
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'delay_minutes',
                                            'label': '延迟执行时间（分钟）',
                                            'type': 'number',
                                            'hint': '事件触发后延迟多少分钟执行同步'
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
            "sync_paths": "",
            "delay_minutes": 5,
            "enable_immediate_execution": True,
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
            "max_file_size": 0,
            "min_file_size": 0,
            "enable_progress": True,
            "max_workers": 4,
            "batch_size": 100,
            "trigger_events": ["transfer_complete"],
            "event_conditions": ""
        }

    # API 端点方法
    def sync_status(self) -> Dict[str, Any]:
        """获取同步状态API"""
        try:
            return {
                "enabled": self._enabled,
                "sync_paths_count": len(self._sync_paths),
                "event_statistics": self._event_manager.get_event_statistics(),
                "pending_syncs": self._event_manager.get_pending_syncs(),
                "performance_metrics": self._service_manager.get_performance_metrics(),
                "health_status": self._service_manager.get_health_status()
            }
        except Exception as e:
            logger.error(f"获取同步状态失败: {str(e)}")
            return {"error": str(e)}

    def manual_sync(self) -> Dict[str, Any]:
        """手动触发同步API"""
        try:
            if not self._enabled:
                return {"error": "插件未启用"}

            if not self._sync_paths:
                return {"error": "未配置同步路径"}

            # 触发手动同步
            sync_count = 0
            for sync_config in self._sync_paths:
                if self._sync_ops:
                    self._sync_ops.execute_sync(sync_config, sync_config['source'])
                    sync_count += 1

            return {
                "success": True,
                "message": f"已触发 {sync_count} 个同步任务"
            }
        except Exception as e:
            logger.error(f"手动触发同步失败: {str(e)}")
            return {"error": str(e)}

    def get_page(self) -> List[dict]:
        """获取插件页面"""
        return [
            {
                'component': 'div',
                'text': '整理后同步插件',
                'props': {'class': 'text-center'}
            },
            {
                'component': 'VCard',
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {'class': 'pe-2'},
                        'content': [
                            {
                                'component': 'VIcon',
                                'props': {'icon': 'mdi-sync', 'class': 'me-2'}
                            },
                            {
                                'component': 'span',
                                'text': '同步状态'
                            }
                        ]
                    },
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'div',
                                'text': f'插件状态: {"启用" if self._enabled else "禁用"}',
                                'props': {'class': 'mb-2'}
                            },
                            {
                                'component': 'div',
                                'text': f'配置的同步路径数量: {len(self._sync_paths)}',
                                'props': {'class': 'mb-2'}
                            }
                        ]
                    }
                ]
            }
        ]

    # 兼容模式方法
    def _apply_config_compatible(self, config: Dict[str, Any]):
        """兼容模式的配置应用"""
        self._enabled = config.get("enabled", False)

        # 解析同步路径（兼容模式）
        sync_paths_str = config.get("sync_paths", "")
        if sync_paths_str:
            self._sync_paths = self._parse_sync_paths_compatible(sync_paths_str)
        else:
            # 向后兼容旧版本配置
            root_path = config.get("sync_root_path", "")
            target_path = config.get("sync_target_path", "")
            if root_path and target_path:
                self._sync_paths = [{"source": root_path, "target": target_path}]
            else:
                self._sync_paths = []

        self._delay_minutes = config.get("delay_minutes", 5)
        self._enable_immediate_execution = config.get("enable_immediate_execution", True)
        self._enable_incremental = config.get("enable_incremental", False)
        self._incremental_cron = config.get("incremental_cron", "0 */6 * * *")
        self._enable_full_sync = config.get("enable_full_sync", False)
        self._full_sync_cron = config.get("full_sync_cron", "0 2 * * 0")
        self._enable_notifications = config.get("enable_notifications", False)
        self._notification_channels = config.get("notification_channels", [])

        # 解析枚举值
        try:
            self._sync_strategy = SyncStrategy(config.get("sync_strategy", "copy"))
        except ValueError:
            self._sync_strategy = SyncStrategy.COPY

        try:
            self._sync_mode = SyncMode(config.get("sync_mode", "immediate"))
        except ValueError:
            self._sync_mode = SyncMode.IMMEDIATE

        self._max_depth = config.get("max_depth", -1)
        self._file_filters = self._parse_list_compatible(config.get("file_filters", ""))
        self._exclude_patterns = self._parse_list_compatible(config.get("exclude_patterns", ""))
        self._max_file_size = config.get("max_file_size", 0)
        self._min_file_size = config.get("min_file_size", 0)
        self._enable_progress = config.get("enable_progress", True)
        self._max_workers = config.get("max_workers", 4)
        self._batch_size = config.get("batch_size", 100)

        # 解析触发事件
        trigger_events_config = config.get("trigger_events", [])
        if isinstance(trigger_events_config, list):
            self._trigger_events = [TriggerEvent(val) for val in trigger_events_config if self._is_valid_event_compatible(val)]
        elif isinstance(trigger_events_config, str) and trigger_events_config:
            event_values = self._parse_list_compatible(trigger_events_config)
            self._trigger_events = [TriggerEvent(val) for val in event_values if self._is_valid_event_compatible(val)]
        else:
            self._trigger_events = [TriggerEvent.TRANSFER_COMPLETE]

        self._event_conditions = config.get("event_conditions", {})

    def _parse_sync_paths_compatible(self, sync_paths_str: str) -> List[Dict[str, str]]:
        """兼容模式的同步路径解析"""
        sync_paths = []
        if not sync_paths_str:
            return sync_paths

        try:
            for path_pair in sync_paths_str.split(','):
                path_pair = path_pair.strip()
                if not path_pair:
                    continue

                # 支持两种分隔符
                if '->' in path_pair:
                    parts = path_pair.split('->', 1)
                elif '::' in path_pair:
                    parts = path_pair.split('::', 1)
                else:
                    logger.warning(f"无效的路径配置格式: {path_pair}")
                    continue

                if len(parts) == 2:
                    source = parts[0].strip()
                    target = parts[1].strip()

                    if source and target:
                        sync_paths.append({
                            "source": source,
                            "target": target
                        })
                    else:
                        logger.warning(f"源路径或目标路径为空: {path_pair}")
                else:
                    logger.warning(f"无效的路径配置: {path_pair}")

        except Exception as e:
            logger.error(f"解析同步路径配置失败: {str(e)}")

        return sync_paths

    def _parse_list_compatible(self, list_str: str, separator: str = ',') -> List[str]:
        """兼容模式的列表解析"""
        if not list_str:
            return []
        return [item.strip() for item in list_str.split(separator) if item.strip()]

    def _is_valid_event_compatible(self, event_value: str) -> bool:
        """兼容模式的事件验证"""
        if not event_value:
            return False
        try:
            TriggerEvent(event_value)
            return True
        except ValueError:
            return False

    def _register_event_listeners_compatible(self):
        """兼容模式的事件监听器注册"""
        try:
            from app.core.event import eventmanager, EventType

            for trigger_event in self._trigger_events:
                if trigger_event == TriggerEvent.TRANSFER_COMPLETE:
                    eventmanager.register(EventType.TransferComplete, self._on_transfer_complete_compatible)
                elif trigger_event == TriggerEvent.DOWNLOAD_ADDED:
                    eventmanager.register(EventType.DownloadAdded, self._on_download_added_compatible)
                # 可以继续添加其他事件类型

            logger.info(f"兼容模式：已注册 {len(self._trigger_events)} 个事件监听器")
        except Exception as e:
            logger.error(f"兼容模式：注册事件监听器失败: {str(e)}")

    def _on_transfer_complete_compatible(self, event):
        """兼容模式的整理完成事件处理"""
        try:
            # 简化的事件处理逻辑
            if hasattr(event, 'event_data') and event.event_data:
                event_data = event.event_data
                # 尝试提取路径
                path = None
                if isinstance(event_data, dict):
                    path = event_data.get('path') or event_data.get('src') or event_data.get('filepath')
                elif hasattr(event_data, 'path'):
                    path = str(event_data.path)

                if path and self._sync_ops:
                    # 使用现有的同步逻辑
                    self._sync_ops.sync_directory(path)
        except Exception as e:
            logger.error(f"兼容模式：处理整理完成事件失败: {str(e)}")

    def _on_download_added_compatible(self, event):
        """兼容模式的下载添加事件处理"""
        try:
            # 简化的事件处理逻辑，类似于 transfer_complete
            self._on_transfer_complete_compatible(event)
        except Exception as e:
            logger.error(f"兼容模式：处理下载添加事件失败: {str(e)}")

    def stop_service(self):
        """停止服务"""
        try:
            if self._use_advanced_features and self._event_manager:
                # 取消事件监听
                self._event_manager.unregister_event_listeners()
                # 清理服务管理器
                self._service_manager.shutdown()
            else:
                # 兼容模式清理
                try:
                    from app.core.event import eventmanager, EventType
                    # 这里应该取消注册，但为了简化暂时跳过
                    pass
                except Exception:
                    pass

            logger.info("TransferSync服务已停止")
        except Exception as e:
            logger.error(f"停止TransferSync服务失败: {str(e)}")