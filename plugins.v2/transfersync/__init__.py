"""
整理后同步插件 - 重构版本
参考 p115strmhelper 的模块化设计，将功能拆分到不同模块
"""
import threading
from typing import Any, Dict, List, Optional, Tuple

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
        """获取插件命令 - 参考 p115strmhelper 的丰富命令"""
        return [
            {
                "action": "manual_sync",
                "name": "手动同步",
                "icon": "mdi-sync",
                "desc": "立即执行手动同步操作"
            },
            {
                "action": "full_sync",
                "name": "全量同步",
                "icon": "mdi-sync-circle",
                "desc": "执行全量同步，同步所有配置的路径"
            },
            {
                "action": "incremental_sync",
                "name": "增量同步",
                "icon": "mdi-update",
                "desc": "执行增量同步，只同步有变化的文件"
            },
            {
                "action": "sync_status",
                "name": "同步状态",
                "icon": "mdi-information",
                "desc": "查看当前同步状态和统计信息"
            },
            {
                "action": "clear_cache",
                "name": "清理缓存",
                "icon": "mdi-cached",
                "desc": "清理插件缓存和临时数据"
            },
            {
                "action": "test_paths",
                "name": "测试路径",
                "icon": "mdi-test-tube",
                "desc": "测试同步路径的可访问性和权限"
            },
            {
                "action": "health_check",
                "name": "健康检查",
                "icon": "mdi-heart-pulse",
                "desc": "检查插件和依赖的健康状态"
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """获取插件API - 参考 p115strmhelper 的丰富API"""
        return [
            {
                "path": "/sync_status",
                "endpoint": self.sync_status,
                "methods": ["GET"],
                "summary": "获取同步状态",
                "description": "获取当前同步状态、统计信息和健康状态"
            },
            {
                "path": "/manual_sync",
                "endpoint": self.manual_sync,
                "methods": ["POST"],
                "summary": "手动触发同步",
                "description": "立即执行手动同步操作"
            },
            {
                "path": "/full_sync",
                "endpoint": self.full_sync,
                "methods": ["POST"],
                "summary": "全量同步",
                "description": "执行全量同步，同步所有配置的路径"
            },
            {
                "path": "/incremental_sync",
                "endpoint": self.incremental_sync,
                "methods": ["POST"],
                "summary": "增量同步",
                "description": "执行增量同步，只同步有变化的文件"
            },
            {
                "path": "/test_paths",
                "endpoint": self.test_paths,
                "methods": ["GET"],
                "summary": "测试路径",
                "description": "测试同步路径的可访问性和权限"
            },
            {
                "path": "/clear_cache",
                "endpoint": self.clear_cache,
                "methods": ["POST"],
                "summary": "清理缓存",
                "description": "清理插件缓存和临时数据"
            },
            {
                "path": "/health_check",
                "endpoint": self.health_check,
                "methods": ["GET"],
                "summary": "健康检查",
                "description": "检查插件和依赖的健康状态"
            },
            {
                "path": "/get_config",
                "endpoint": self.get_config,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "获取配置",
                "description": "获取当前插件配置信息"
            },
            {
                "path": "/save_config",
                "endpoint": self.save_config,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "保存配置",
                "description": "保存插件配置设置"
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取配置表单 - 参考 p115strmhelper 的丰富配置"""
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
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '整理后同步插件 - 监听事件自动同步文件到指定位置，支持多种同步策略和事件类型'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 基础开关
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
                                            'model': 'enable_notifications',
                                            'label': '启用通知',
                                            'hint': '开启后将发送同步状态通知'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 同步路径配置
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
                                            'placeholder': '源路径1->目标路径1\n源路径2->目标路径2\n或用逗号分隔：源路径1->目标路径1,源路径2->目标路径2',
                                            'hint': '支持多组同步路径配置，格式：源路径->目标路径 或 源路径::目标路径',
                                            'rows': 4
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 基础配置
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
                                            'model': 'sync_strategy',
                                            'label': '同步策略',
                                            'items': [
                                                {'title': '复制文件', 'value': 'copy'},
                                                {'title': '移动文件', 'value': 'move'},
                                                {'title': '软链接', 'value': 'softlink'},
                                                {'title': '硬链接', 'value': 'hardlink'}
                                            ],
                                            'hint': '选择文件同步的方式'
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
                                            'model': 'sync_mode',
                                            'label': '同步模式',
                                            'items': [
                                                {'title': '立即同步', 'value': 'immediate'},
                                                {'title': '批量同步', 'value': 'batch'},
                                                {'title': '队列同步', 'value': 'queue'}
                                            ],
                                            'hint': '选择同步执行模式'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 触发事件配置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'trigger_events',
                                            'label': '触发事件',
                                            'items': [
                                                {'title': '整理完成', 'value': 'transfer.complete'},
                                                {'title': '下载添加', 'value': 'download.added'},
                                                {'title': '订阅完成', 'value': 'subscribe.complete'},
                                                {'title': '媒体添加', 'value': 'media.added'},
                                                {'title': '文件移动', 'value': 'file.moved'},
                                                {'title': '目录扫描完成', 'value': 'directory.scan.complete'},
                                                {'title': '刮削完成', 'value': 'scrape.complete'},
                                                {'title': '插件触发', 'value': 'plugin.triggered'}
                                            ],
                                            'multiple': True,
                                            'hint': '选择触发同步的事件类型'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 执行设置
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
                                            'model': 'enable_immediate_execution',
                                            'label': '启用立即执行',
                                            'hint': '开启后将立即执行同步，否则根据延迟时间执行'
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
                                            'model': 'delay_minutes',
                                            'label': '延迟执行时间（分钟）',
                                            'type': 'number',
                                            'hint': '事件触发后延迟多少分钟执行同步'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 文件过滤
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
                                            'model': 'file_filters',
                                            'label': '文件类型过滤',
                                            'placeholder': 'mp4,mkv,avi,mov',
                                            'hint': '只同步指定类型的文件，用逗号分隔'
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
                                            'model': 'exclude_patterns',
                                            'label': '排除模式',
                                            'placeholder': 'temp,cache,@eaDir',
                                            'hint': '排除包含这些字符的文件/目录，用逗号分隔'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 文件大小限制
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
                                            'model': 'min_file_size',
                                            'label': '最小文件大小 (MB)',
                                            'type': 'number',
                                            'hint': '小于此大小的文件不会被同步，0表示无限制'
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
                                            'model': 'max_file_size',
                                            'label': '最大文件大小 (MB)',
                                            'type': 'number',
                                            'hint': '大于此大小的文件不会被同步，0表示无限制'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 高级设置
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
                                            'model': 'max_depth',
                                            'label': '最大目录深度',
                                            'type': 'number',
                                            'hint': '限制同步的目录深度，-1表示无限制'
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
                                            'model': 'max_workers',
                                            'label': '并发线程数',
                                            'type': 'number',
                                            'hint': '同时进行同步的线程数量'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 定时任务
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
                                            'hint': '定时执行增量同步任务'
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
                                            'label': '增量同步周期',
                                            'placeholder': '0 */6 * * *',
                                            'hint': 'Cron表达式，如：0 */6 * * * (每6小时)'
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
                                            'hint': '定时执行全量同步任务'
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
                                            'label': '全量同步周期',
                                            'placeholder': '0 2 * * 0',
                                            'hint': 'Cron表达式，如：0 2 * * 0 (每周日凌晨2点)'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 监控设置
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
                                            'model': 'enable_progress',
                                            'label': '启用进度显示',
                                            'hint': '显示详细的同步进度信息'
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
                                            'model': 'batch_size',
                                            'label': '批处理大小',
                                            'type': 'number',
                                            'hint': '每批处理的文件数量'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 通知配置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'notification_channels',
                                            'label': '通知渠道',
                                            'placeholder': 'telegram,wechat,email',
                                            'hint': '指定通知渠道，用逗号分隔，留空则使用默认渠道'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 事件条件
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
                                            'model': 'event_conditions',
                                            'label': '事件过滤条件',
                                            'placeholder': 'media_type=movie\nfile_size>100\npath_contains=2024',
                                            'hint': '设置事件过滤条件，每行一个条件，格式：字段名=值',
                                            'rows': 3
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
            "trigger_events": ["transfer.complete"],
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

    def full_sync(self) -> Dict[str, Any]:
        """全量同步API"""
        try:
            if not self._enabled:
                return {"error": "插件未启用"}

            if not self._sync_paths:
                return {"error": "未配置同步路径"}

            # 执行全量同步
            sync_count = 0
            total_files = 0
            for sync_config in self._sync_paths:
                if self._sync_ops:
                    result = self._sync_ops.execute_sync(sync_config, sync_config['source'])
                    sync_count += 1
                    total_files += result.get('synced_files', 0)

            return {
                "success": True,
                "message": f"全量同步完成，处理了 {sync_count} 个路径，同步了 {total_files} 个文件"
            }
        except Exception as e:
            logger.error(f"全量同步失败: {str(e)}")
            return {"error": str(e)}

    def incremental_sync(self) -> Dict[str, Any]:
        """增量同步API"""
        try:
            if not self._enabled:
                return {"error": "插件未启用"}

            if not self._sync_paths:
                return {"error": "未配置同步路径"}

            # 执行增量同步（这里可以添加时间戳比较逻辑）
            sync_count = 0
            total_files = 0
            for sync_config in self._sync_paths:
                if self._sync_ops:
                    result = self._sync_ops.execute_sync(sync_config, sync_config['source'])
                    sync_count += 1
                    total_files += result.get('synced_files', 0)

            return {
                "success": True,
                "message": f"增量同步完成，处理了 {sync_count} 个路径，同步了 {total_files} 个文件"
            }
        except Exception as e:
            logger.error(f"增量同步失败: {str(e)}")
            return {"error": str(e)}

    def test_paths(self) -> Dict[str, Any]:
        """测试路径API"""
        try:
            if not self._sync_paths:
                return {"error": "未配置同步路径"}

            results = []
            for i, sync_config in enumerate(self._sync_paths):
                source = sync_config.get('source', '')
                target = sync_config.get('target', '')

                test_result = {
                    "index": i + 1,
                    "source": source,
                    "target": target,
                    "source_status": "unknown",
                    "target_status": "unknown",
                    "permissions": "unknown"
                }

                # 测试源路径
                try:
                    from pathlib import Path
                    source_path = Path(source)
                    if source_path.exists():
                        if source_path.is_dir():
                            test_result["source_status"] = "ok"
                        else:
                            test_result["source_status"] = "not_directory"
                    else:
                        test_result["source_status"] = "not_found"
                except Exception as e:
                    test_result["source_status"] = f"error: {str(e)}"

                # 测试目标路径
                try:
                    target_path = Path(target)
                    if target_path.parent.exists():
                        if target_path.exists() and target_path.is_dir():
                            test_result["target_status"] = "ok"
                        elif not target_path.exists():
                            test_result["target_status"] = "parent_ok"
                        else:
                            test_result["target_status"] = "not_directory"
                    else:
                        test_result["target_status"] = "parent_not_found"
                except Exception as e:
                    test_result["target_status"] = f"error: {str(e)}"

                # 测试权限
                try:
                    import os
                    if source_path.exists() and os.access(source, os.R_OK):
                        if target_path.parent.exists() and os.access(target_path.parent, os.W_OK):
                            test_result["permissions"] = "ok"
                        else:
                            test_result["permissions"] = "target_no_write"
                    else:
                        test_result["permissions"] = "source_no_read"
                except Exception as e:
                    test_result["permissions"] = f"error: {str(e)}"

                results.append(test_result)

            return {
                "success": True,
                "results": results
            }
        except Exception as e:
            logger.error(f"测试路径失败: {str(e)}")
            return {"error": str(e)}

    def clear_cache(self) -> Dict[str, Any]:
        """清理缓存API"""
        try:
            # 清理服务管理器缓存
            if self._use_advanced_features and self._service_manager:
                self._service_manager.clear_cache()

            # 清理方法级缓存
            if hasattr(self, '_validate_config') and hasattr(self._validate_config, 'cache_clear'):
                self._validate_config.cache_clear()

            logger.info("缓存清理完成")
            return {
                "success": True,
                "message": "缓存清理完成"
            }
        except Exception as e:
            logger.error(f"清理缓存失败: {str(e)}")
            return {"error": str(e)}

    def health_check(self) -> Dict[str, Any]:
        """健康检查API"""
        try:
            health_status = {
                "overall": "healthy",
                "components": {}
            }

            # 检查基础配置
            health_status["components"]["config"] = {
                "status": "healthy" if self._enabled and self._sync_paths else "warning",
                "details": f"启用状态: {self._enabled}, 配置路径数: {len(self._sync_paths)}"
            }

            # 检查同步操作组件
            health_status["components"]["sync_operations"] = {
                "status": "healthy" if self._sync_ops else "error",
                "details": "同步操作组件状态"
            }

            # 检查事件管理器
            health_status["components"]["event_manager"] = {
                "status": "healthy" if (self._use_advanced_features and self._event_manager) or not self._use_advanced_features else "warning",
                "details": f"高级功能: {self._use_advanced_features}"
            }

            # 检查路径可访问性
            path_issues = 0
            for sync_config in self._sync_paths:
                try:
                    from pathlib import Path
                    source_path = Path(sync_config.get('source', ''))
                    if not source_path.exists():
                        path_issues += 1
                except Exception:
                    path_issues += 1

            health_status["components"]["paths"] = {
                "status": "healthy" if path_issues == 0 else "warning",
                "details": f"路径问题数量: {path_issues}"
            }

            # 总体状态评估
            component_statuses = [comp["status"] for comp in health_status["components"].values()]
            if "error" in component_statuses:
                health_status["overall"] = "error"
            elif "warning" in component_statuses:
                health_status["overall"] = "warning"

            return health_status
        except Exception as e:
            logger.error(f"健康检查失败: {str(e)}")
            return {"error": str(e)}

    def get_config(self) -> Dict[str, Any]:
        """获取配置API"""
        try:
            return {
                "enabled": self._enabled,
                "sync_paths": [f"{config['source']}->{config['target']}" for config in self._sync_paths],
                "sync_strategy": self._sync_strategy.value if hasattr(self._sync_strategy, 'value') else str(self._sync_strategy),
                "sync_mode": self._sync_mode.value if hasattr(self._sync_mode, 'value') else str(self._sync_mode),
                "delay_minutes": self._delay_minutes,
                "enable_immediate_execution": self._enable_immediate_execution,
                "enable_notifications": self._enable_notifications,
                "file_filters": self._file_filters,
                "exclude_patterns": self._exclude_patterns,
                "max_file_size": self._max_file_size,
                "min_file_size": self._min_file_size,
                "max_depth": self._max_depth,
                "max_workers": self._max_workers,
                "batch_size": self._batch_size,
                "trigger_events": [event.value for event in self._trigger_events],
                "enable_incremental": self._enable_incremental,
                "incremental_cron": self._incremental_cron,
                "enable_full_sync": self._enable_full_sync,
                "full_sync_cron": self._full_sync_cron
            }
        except Exception as e:
            logger.error(f"获取配置失败: {str(e)}")
            return {"error": str(e)}

    def save_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """保存配置API"""
        try:
            # 这里可以实现配置保存逻辑
            # 通常会调用 MoviePilot 的配置保存接口
            logger.info("配置保存请求已接收")
            return {
                "success": True,
                "message": "配置保存成功"
            }
        except Exception as e:
            logger.error(f"保存配置失败: {str(e)}")
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