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
from .core.config_manager import ConfigManager
from .core.event_manager import EventManager
from .core.service_manager import ServiceManager

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
        self._config_manager = ConfigManager()
        self._event_manager = EventManager(self)
        self._service_manager = ServiceManager()

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
                # 使用配置管理器解析配置
                parsed_config = self._config_manager.parse_config(config)
                self._apply_config(parsed_config)

            # 初始化功能模块
            self._sync_ops = SyncOperations(self)

            # 注册事件监听器
            if self._enabled:
                self._event_manager.register_event_listeners(self._trigger_events)
                logger.info("TransferSync插件已启用，事件监听器已注册")

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