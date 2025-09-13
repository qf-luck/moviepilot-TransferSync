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
    plugin_desc = "监听多种事件类型自动同步文件到指定位置，支持多种同步策略，默认启用增量同步，简化配置更易使用。"
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

        # 配置属性 - 简化版本，移除不必要的选项
        self._sync_paths = []  # 多路径支持
        self._delay_minutes = 5
        self._enable_immediate_execution = True
        self._incremental_cron = "0 */6 * * *"  # 默认启用增量同步
        self._enable_notifications = False
        self._sync_strategy = SyncStrategy.COPY
        self._sync_mode = SyncMode.IMMEDIATE
        self._max_depth = -1
        self._file_filters = []
        self._exclude_patterns = []
        self._max_workers = 4
        self._trigger_events = [TriggerEvent.TRANSFER_COMPLETE]

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
        """应用解析后的配置 - 简化版本"""
        self._enabled = config.get("enabled", False)
        self._sync_paths = config.get("sync_paths", [])
        self._delay_minutes = config.get("delay_minutes", 5)
        self._enable_immediate_execution = config.get("enable_immediate_execution", True)
        self._incremental_cron = config.get("incremental_cron", "0 */6 * * *")
        self._enable_notifications = config.get("enable_notifications", False)
        self._sync_strategy = config.get("sync_strategy", SyncStrategy.COPY)
        self._sync_mode = config.get("sync_mode", SyncMode.IMMEDIATE)
        self._max_depth = config.get("max_depth", -1)
        self._file_filters = config.get("file_filters", [])
        self._exclude_patterns = config.get("exclude_patterns", [])
        self._max_workers = config.get("max_workers", 4)
        self._trigger_events = config.get("trigger_events", [TriggerEvent.TRANSFER_COMPLETE])

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
        """获取插件命令 - 简化版本"""
        return [
            {
                "action": "manual_sync",
                "name": "手动同步",
                "icon": "mdi-sync",
                "desc": "立即执行手动同步操作"
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
                "action": "test_paths",
                "name": "测试路径",
                "icon": "mdi-test-tube",
                "desc": "测试同步路径的可访问性和权限"
            }
        ]

    def get_api(self) -> List[Dict[str, Any]]:
        """获取插件API - 简化版本"""
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
            }
        ]

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取配置表单 - 简化版本，移除不必要的选项"""
        return [
            {
                'component': 'div',
                'props': {
                    'class': 'form-container'
                },
                'content': [
                    # 插件说明
                    {
                        'component': 'div',
                        'props': {
                            'class': 'alert alert-info'
                        },
                        'text': '整理后同步插件 - 监听事件自动同步文件到指定位置，默认启用增量同步'
                    },
                    # 基础开关
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-row'
                        },
                        'content': [
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group'
                                },
                                'content': [
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'checkbox',
                                            'id': 'enabled',
                                            'name': 'enabled',
                                            'value': True
                                        }
                                    },
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'enabled'
                                        },
                                        'text': '启用插件'
                                    }
                                ]
                            },
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group'
                                },
                                'content': [
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'checkbox',
                                            'id': 'enable_notifications',
                                            'name': 'enable_notifications',
                                            'value': False
                                        }
                                    },
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'enable_notifications'
                                        },
                                        'text': '启用通知'
                                    }
                                ]
                            }
                        ]
                    },
                    # 同步路径配置
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-group'
                        },
                        'content': [
                            {
                                'component': 'label',
                                'props': {
                                    'for': 'sync_paths'
                                },
                                'text': '同步路径配置'
                            },
                            {
                                'component': 'textarea',
                                'props': {
                                    'id': 'sync_paths',
                                    'name': 'sync_paths',
                                    'rows': 4,
                                    'placeholder': '源路径1->目标路径1\n源路径2->目标路径2\n或用逗号分隔多组配置',
                                    'class': 'form-control'
                                }
                            },
                            {
                                'component': 'small',
                                'props': {
                                    'class': 'form-text text-muted'
                                },
                                'text': '支持多组同步路径配置，格式：源路径->目标路径 或 源路径::目标路径'
                            }
                        ]
                    },
                    # 基础配置
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-row'
                        },
                        'content': [
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'sync_strategy'
                                        },
                                        'text': '同步策略'
                                    },
                                    {
                                        'component': 'select',
                                        'props': {
                                            'id': 'sync_strategy',
                                            'name': 'sync_strategy',
                                            'class': 'form-control'
                                        },
                                        'content': [
                                            {
                                                'component': 'option',
                                                'props': {
                                                    'value': 'copy'
                                                },
                                                'text': '复制文件'
                                            },
                                            {
                                                'component': 'option',
                                                'props': {
                                                    'value': 'move'
                                                },
                                                'text': '移动文件'
                                            },
                                            {
                                                'component': 'option',
                                                'props': {
                                                    'value': 'softlink'
                                                },
                                                'text': '软链接'
                                            },
                                            {
                                                'component': 'option',
                                                'props': {
                                                    'value': 'hardlink'
                                                },
                                                'text': '硬链接'
                                            }
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'sync_mode'
                                        },
                                        'text': '同步模式'
                                    },
                                    {
                                        'component': 'select',
                                        'props': {
                                            'id': 'sync_mode',
                                            'name': 'sync_mode',
                                            'class': 'form-control'
                                        },
                                        'content': [
                                            {
                                                'component': 'option',
                                                'props': {
                                                    'value': 'immediate'
                                                },
                                                'text': '立即同步'
                                            },
                                            {
                                                'component': 'option',
                                                'props': {
                                                    'value': 'batch'
                                                },
                                                'text': '批量同步'
                                            },
                                            {
                                                'component': 'option',
                                                'props': {
                                                    'value': 'queue'
                                                },
                                                'text': '队列同步'
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    # 触发事件配置
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-group'
                        },
                        'content': [
                            {
                                'component': 'label',
                                'props': {
                                    'for': 'trigger_events'
                                },
                                'text': '触发事件'
                            },
                            {
                                'component': 'select',
                                'props': {
                                    'id': 'trigger_events',
                                    'name': 'trigger_events',
                                    'multiple': True,
                                    'class': 'form-control'
                                },
                                'content': [
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'transfer.complete'
                                        },
                                        'text': '整理完成'
                                    },
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'download.added'
                                        },
                                        'text': '下载添加'
                                    },
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'subscribe.complete'
                                        },
                                        'text': '订阅完成'
                                    },
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'media.added'
                                        },
                                        'text': '媒体添加'
                                    },
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'file.moved'
                                        },
                                        'text': '文件移动'
                                    },
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'directory.scan.complete'
                                        },
                                        'text': '目录扫描完成'
                                    },
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'scrape.complete'
                                        },
                                        'text': '刮削完成'
                                    },
                                    {
                                        'component': 'option',
                                        'props': {
                                            'value': 'plugin.triggered'
                                        },
                                        'text': '插件触发'
                                    }
                                ]
                            },
                            {
                                'component': 'small',
                                'props': {
                                    'class': 'form-text text-muted'
                                },
                                'text': '按住Ctrl键可选择多个事件类型'
                            }
                        ]
                    },
                    # 执行设置
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-row'
                        },
                        'content': [
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'checkbox',
                                            'id': 'enable_immediate_execution',
                                            'name': 'enable_immediate_execution',
                                            'value': True
                                        }
                                    },
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'enable_immediate_execution'
                                        },
                                        'text': '启用立即执行'
                                    }
                                ]
                            },
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'delay_minutes'
                                        },
                                        'text': '延迟执行时间（分钟）'
                                    },
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'number',
                                            'id': 'delay_minutes',
                                            'name': 'delay_minutes',
                                            'value': 5,
                                            'class': 'form-control'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 增量同步周期配置
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-group'
                        },
                        'content': [
                            {
                                'component': 'label',
                                'props': {
                                    'for': 'incremental_cron'
                                },
                                'text': '增量同步周期'
                            },
                            {
                                'component': 'input',
                                'props': {
                                    'type': 'text',
                                    'id': 'incremental_cron',
                                    'name': 'incremental_cron',
                                    'value': '0 */6 * * *',
                                    'placeholder': '0 */6 * * *',
                                    'class': 'form-control'
                                }
                            },
                            {
                                'component': 'small',
                                'props': {
                                    'class': 'form-text text-muted'
                                },
                                'text': 'Cron表达式，默认每6小时执行一次增量同步'
                            }
                        ]
                    },
                    # 文件过滤
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-row'
                        },
                        'content': [
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'file_filters'
                                        },
                                        'text': '文件类型过滤'
                                    },
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'text',
                                            'id': 'file_filters',
                                            'name': 'file_filters',
                                            'placeholder': 'mp4,mkv,avi,mov',
                                            'class': 'form-control'
                                        }
                                    },
                                    {
                                        'component': 'small',
                                        'props': {
                                            'class': 'form-text text-muted'
                                        },
                                        'text': '只同步指定类型的文件，用逗号分隔'
                                    }
                                ]
                            },
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'exclude_patterns'
                                        },
                                        'text': '排除模式'
                                    },
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'text',
                                            'id': 'exclude_patterns',
                                            'name': 'exclude_patterns',
                                            'placeholder': 'temp,cache,@eaDir',
                                            'class': 'form-control'
                                        }
                                    },
                                    {
                                        'component': 'small',
                                        'props': {
                                            'class': 'form-text text-muted'
                                        },
                                        'text': '排除包含这些字符的文件/目录，用逗号分隔'
                                    }
                                ]
                            }
                        ]
                    },
                    # 高级设置
                    {
                        'component': 'div',
                        'props': {
                            'class': 'form-row'
                        },
                        'content': [
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'max_depth'
                                        },
                                        'text': '最大目录深度'
                                    },
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'number',
                                            'id': 'max_depth',
                                            'name': 'max_depth',
                                            'value': -1,
                                            'class': 'form-control'
                                        }
                                    },
                                    {
                                        'component': 'small',
                                        'props': {
                                            'class': 'form-text text-muted'
                                        },
                                        'text': '限制同步的目录深度，-1表示无限制'
                                    }
                                ]
                            },
                            {
                                'component': 'div',
                                'props': {
                                    'class': 'form-group col-md-6'
                                },
                                'content': [
                                    {
                                        'component': 'label',
                                        'props': {
                                            'for': 'max_workers'
                                        },
                                        'text': '并发线程数'
                                    },
                                    {
                                        'component': 'input',
                                        'props': {
                                            'type': 'number',
                                            'id': 'max_workers',
                                            'name': 'max_workers',
                                            'value': 4,
                                            'class': 'form-control'
                                        }
                                    },
                                    {
                                        'component': 'small',
                                        'props': {
                                            'class': 'form-text text-muted'
                                        },
                                        'text': '同时进行同步的线程数量'
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
            "incremental_cron": "0 */6 * * *",
            "enable_notifications": False,
            "sync_strategy": "copy",
            "sync_mode": "immediate",
            "max_depth": -1,
            "file_filters": "",
            "exclude_patterns": "",
            "max_workers": 4,
            "trigger_events": ["transfer.complete"]
        }

    # API 端点方法
    def sync_status(self) -> Dict[str, Any]:
        """获取同步状态API - 简化版本"""
        try:
            status = {
                "enabled": self._enabled,
                "sync_paths_count": len(self._sync_paths),
                "sync_strategy": self._sync_strategy.value if hasattr(self._sync_strategy, 'value') else str(self._sync_strategy),
                "incremental_cron": self._incremental_cron,
                "immediate_execution": self._enable_immediate_execution
            }

            # 如果使用高级功能，添加更多状态信息
            if self._use_advanced_features and self._event_manager:
                try:
                    status["event_statistics"] = self._event_manager.get_event_statistics()
                    status["pending_syncs"] = self._event_manager.get_pending_syncs()
                except Exception:
                    pass

            return status
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
        """兼容模式的配置应用 - 简化版本"""
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
        self._incremental_cron = config.get("incremental_cron", "0 */6 * * *")
        self._enable_notifications = config.get("enable_notifications", False)

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
        self._max_workers = config.get("max_workers", 4)

        # 解析触发事件
        trigger_events_config = config.get("trigger_events", [])
        if isinstance(trigger_events_config, list):
            self._trigger_events = [TriggerEvent(val) for val in trigger_events_config if self._is_valid_event_compatible(val)]
        elif isinstance(trigger_events_config, str) and trigger_events_config:
            event_values = self._parse_list_compatible(trigger_events_config)
            self._trigger_events = [TriggerEvent(val) for val in event_values if self._is_valid_event_compatible(val)]
        else:
            self._trigger_events = [TriggerEvent.TRANSFER_COMPLETE]

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

    def get_service(self) -> List[Dict[str, Any]]:
        """获取插件服务 - 启用默认增量同步"""
        if not self._enabled or not self._sync_paths:
            return []

        services = []

        # 默认启用增量同步定时任务
        if self._incremental_cron:
            services.append({
                "id": "TransferSync_incremental_sync",
                "name": "增量同步任务",
                "trigger": "cron",
                "cron": self._incremental_cron,
                "func": self.incremental_sync,
                "kwargs": {}
            })

        return services

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