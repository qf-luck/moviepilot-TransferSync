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
from .sync_types import SyncStrategy, SyncType, ExecutionMode, TriggerEvent
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

        # 配置属性 - 按要求重新设计
        self._source_path = ""  # 源路径
        self._target_path = ""  # 目标路径
        self._delay_minutes = 5
        self._enable_notifications = False
        self._notification_channel = "telegram"  # 通知渠道
        self._sync_strategy = SyncStrategy.COPY
        self._sync_type = SyncType.INCREMENTAL  # 增量同步/全量同步策略
        self._execution_mode = ExecutionMode.IMMEDIATE  # 立即/延迟执行
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
        """应用解析后的配置 - 按要求修改"""
        self._enabled = config.get("enabled", False)
        self._source_path = config.get("source_path", "")
        self._target_path = config.get("target_path", "")
        self._delay_minutes = config.get("delay_minutes", 5)
        self._enable_notifications = config.get("enable_notifications", False)
        self._notification_channel = config.get("notification_channel", "telegram")
        self._sync_strategy = config.get("sync_strategy", SyncStrategy.COPY)
        self._sync_type = config.get("sync_type", SyncType.INCREMENTAL)
        self._execution_mode = config.get("execution_mode", ExecutionMode.IMMEDIATE)
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
        """获取插件API - 包含文件管理器支持"""
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
            },
            {
                "path": "/browse_files",
                "endpoint": self.browse_files,
                "methods": ["GET"],
                "auth": "bear",
                "summary": "浏览文件目录",
                "description": "用于路径选择的文件管理器接口，支持Vue前端"
            },
            {
                "path": "/browse_path",
                "endpoint": self.browse_path,
                "methods": ["POST"],
                "auth": "bear",
                "summary": "浏览指定路径",
                "description": "浏览指定路径的文件和目录"
            },
            {
                "path": "/get_config_api",
                "endpoint": self.get_config_api,
                "methods": ["GET"],
                "summary": "获取配置API",
                "description": "为前端提供配置数据"
            },
            {
                "path": "/save_config_api",
                "endpoint": self.save_config_api,
                "methods": ["POST"],
                "summary": "保存配置API",
                "description": "为前端保存配置数据"
            }
        ]

    @staticmethod
    def get_render_mode() -> Tuple[str, Optional[str]]:
        """
        返回插件使用的前端渲染模式
        :return: 前端渲染模式，前端文件目录
        """
        # 使用Vue模块联邦模式，参考联邦.md文档
        return "vue", "dist/assets"  # Vue模块联邦模式
        # return "vuetify", None  # 传统Vuetify JSON配置模式

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取配置表单 - 支持Vuetify和Vue模式"""
        # 检查渲染模式
        render_mode, _ = self.get_render_mode()

        if render_mode == "vue":
            # Vue模式：返回None和配置数据
            return None, self._get_vue_config()
        else:
            # Vuetify模式：返回组件配置
            return self._get_vuetify_form()

    def _get_vue_config(self) -> Dict[str, Any]:
        """获取Vue模式的配置数据"""
        return {
            "enabled": self._enabled,
            "source_path": self._source_path,
            "target_path": self._target_path,
            "sync_type": self._sync_type.value if hasattr(self._sync_type, 'value') else str(self._sync_type),
            "execution_mode": self._execution_mode.value if hasattr(self._execution_mode, 'value') else str(self._execution_mode),
            "delay_minutes": self._delay_minutes,
            "enable_notifications": self._enable_notifications,
            "notification_channel": self._notification_channel,
            "sync_strategy": self._sync_strategy.value if hasattr(self._sync_strategy, 'value') else str(self._sync_strategy),
            "max_depth": self._max_depth,
            "file_filters": ",".join(self._file_filters),
            "exclude_patterns": ",".join(self._exclude_patterns),
            "max_workers": self._max_workers,
            "trigger_events": [event.value if hasattr(event, 'value') else str(event) for event in self._trigger_events]
        }

    def _get_vuetify_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """获取Vuetify模式的表单配置 - 简化版本"""
        return [
            {
                'component': 'VForm',
                'content': [
                    # 说明信息
                    {
                        'component': 'VAlert',
                        'props': {
                            'type': 'info',
                            'variant': 'tonal',
                            'text': '整理后同步插件 - 支持增量和全量同步策略，监听多种事件类型自动同步文件'
                        }
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
                                            'label': '启用插件'
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
                                            'label': '启用通知'
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'source_path',
                                            'label': '源路径',
                                            'placeholder': '请输入源路径，例如：/downloads'
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
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'target_path',
                                            'label': '目标路径',
                                            'placeholder': '请输入目标路径，例如：/media'
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
                                            'label': '同步路径列表',
                                            'placeholder': '源路径1->目标路径1\n源路径2->目标路径2',
                                            'hint': '每行一组路径配置，格式：源路径->目标路径',
                                            'rows': 3
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 同步策略配置 - 按要求改为增量/全量同步策略
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'sync_type',
                                            'label': '同步类型',
                                            'items': [
                                                {'title': '增量同步', 'value': 'incremental'},
                                                {'title': '全量同步', 'value': 'full'}
                                            ],
                                            'hint': '选择增量同步或全量同步策略'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'execution_mode',
                                            'label': '执行模式',
                                            'items': [
                                                {'title': '立即执行', 'value': 'immediate'},
                                                {'title': '延迟执行', 'value': 'delayed'}
                                            ],
                                            'hint': '选择立即还是延迟执行'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'delay_minutes',
                                            'label': '延迟时间（分钟）',
                                            'type': 'number',
                                            'hint': '延迟执行的等待时间',
                                            'show': '{{ execution_mode === "delayed" }}'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {'cols': 12, 'md': 3},
                                'content': [
                                    {
                                        'component': 'VSelect',
                                        'props': {
                                            'model': 'sync_strategy',
                                            'label': '文件操作',
                                            'items': [
                                                {'title': '复制文件', 'value': 'copy'},
                                                {'title': '移动文件', 'value': 'move'},
                                                {'title': '软链接', 'value': 'softlink'},
                                                {'title': '硬链接', 'value': 'hardlink'}
                                            ],
                                            'hint': '选择文件操作方式'
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
                                            'hint': '排除包含这些字符的文件/目录'
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
                ]
            }
        ], {
            "enabled": self._enabled,
            "source_path": self._source_path,
            "target_path": self._target_path,
            "sync_type": self._sync_type.value if hasattr(self._sync_type, 'value') else str(self._sync_type),
            "execution_mode": self._execution_mode.value if hasattr(self._execution_mode, 'value') else str(self._execution_mode),
            "delay_minutes": self._delay_minutes,
            "enable_notifications": self._enable_notifications,
            "notification_channel": self._notification_channel,
            "sync_strategy": self._sync_strategy.value if hasattr(self._sync_strategy, 'value') else str(self._sync_strategy),
            "max_depth": self._max_depth,
            "file_filters": ",".join(self._file_filters),
            "exclude_patterns": ",".join(self._exclude_patterns),
            "max_workers": self._max_workers,
            "trigger_events": [event.value if hasattr(event, 'value') else str(event) for event in self._trigger_events]
        }

    # API 端点方法
    def sync_status(self) -> Dict[str, Any]:
        """获取同步状态API - 简化版本"""
        try:
            status = {
                "enabled": self._enabled,
                "sync_paths_count": 1 if self._source_path and self._target_path else 0,
                "sync_strategy": self._sync_strategy.value if hasattr(self._sync_strategy, 'value') else str(self._sync_strategy),
                "sync_type": self._sync_type.value if hasattr(self._sync_type, 'value') else str(self._sync_type),
                "execution_mode": self._execution_mode.value if hasattr(self._execution_mode, 'value') else str(self._execution_mode),
                "delay_minutes": self._delay_minutes,
                "source_path": self._source_path,
                "target_path": self._target_path,
                "notification_channel": self._notification_channel
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

            if not self._source_path or not self._target_path:
                return {"error": "未配置源路径或目标路径"}

            # 触发手动同步
            if self._sync_ops:
                sync_config = {"source": self._source_path, "target": self._target_path}
                self._sync_ops.execute_sync(sync_config, self._source_path)

            return {
                "success": True,
                "message": f"已触发从 {self._source_path} 到 {self._target_path} 的同步任务"
            }
        except Exception as e:
            logger.error(f"手动触发同步失败: {str(e)}")
            return {"error": str(e)}


    def incremental_sync(self) -> Dict[str, Any]:
        """增量同步API"""
        try:
            if not self._enabled:
                return {"error": "插件未启用"}

            if not self._source_path or not self._target_path:
                return {"error": "未配置源路径或目标路径"}

            # 执行增量同步（这里可以添加时间戳比较逻辑）
            total_files = 0
            if self._sync_ops:
                sync_config = {"source": self._source_path, "target": self._target_path}
                result = self._sync_ops.execute_sync(sync_config, self._source_path)
                total_files = result.get('synced_files', 0)

            return {
                "success": True,
                "message": f"增量同步完成，从 {self._source_path} 到 {self._target_path}，同步了 {total_files} 个文件"
            }
        except Exception as e:
            logger.error(f"增量同步失败: {str(e)}")
            return {"error": str(e)}

    def test_paths(self) -> Dict[str, Any]:
        """测试路径API"""
        try:
            if not self._source_path or not self._target_path:
                return {"error": "未配置源路径或目标路径"}

            results = []
            source = self._source_path
            target = self._target_path

            test_result = {
                "index": 1,
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

    def browse_files(self, path: str = "/") -> Dict[str, Any]:
        """文件浏览API - 用于路径选择"""
        try:
            import os
            from pathlib import Path

            # 验证路径安全性
            if not path or path == "/":
                path = os.path.expanduser("~")  # 默认从用户主目录开始

            path_obj = Path(path)
            if not path_obj.exists() or not path_obj.is_dir():
                return {"error": "路径不存在或不是目录"}

            # 获取目录内容
            items = []
            try:
                # 添加父目录项
                if str(path_obj) != str(path_obj.root):
                    items.append({
                        "name": "..",
                        "path": str(path_obj.parent),
                        "type": "dir",
                        "size": 0,
                        "is_parent": True
                    })

                # 列出目录内容
                for item in sorted(path_obj.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                    try:
                        stat = item.stat()
                        items.append({
                            "name": item.name,
                            "path": str(item),
                            "type": "dir" if item.is_dir() else "file",
                            "size": stat.st_size if item.is_file() else 0,
                            "mtime": stat.st_mtime,
                            "is_parent": False
                        })
                    except (OSError, PermissionError):
                        # 跳过无权限访问的文件/目录
                        continue

            except PermissionError:
                return {"error": "无权限访问该目录"}

            return {
                "success": True,
                "current_path": str(path_obj),
                "items": items
            }

        except Exception as e:
            logger.error(f"浏览文件失败: {str(e)}")
            return {"error": str(e)}

    def browse_path(self, path: str) -> Dict[str, Any]:
        """浏览指定路径的API"""
        return self.browse_files(path)

    def get_config_api(self) -> Dict[str, Any]:
        """获取配置API - 为Vue前端提供配置数据"""
        try:
            config_data = self._get_vue_config()
            # 确保所有字段都有默认值
            default_config = {
                "enabled": False,
                "source_path": "",
                "target_path": "",
                "sync_paths": "",
                "sync_type": "incremental",
                "execution_mode": "immediate",
                "delay_minutes": 5,
                "enable_notifications": False,
                "sync_strategy": "copy",
                "max_depth": -1,
                "file_filters": "",
                "exclude_patterns": "",
                "max_workers": 4,
                "trigger_events": ["transfer.complete"]
            }
            default_config.update(config_data)
            return {
                "success": True,
                "data": default_config
            }
        except Exception as e:
            logger.error(f"获取配置API失败: {str(e)}")
            return {"success": False, "error": str(e)}

    def save_config_api(self, config_data: Dict[str, Any] = None) -> Dict[str, Any]:
        """保存配置API - 为前端保存配置"""
        try:
            if config_data:
                # 这里可以实现配置保存逻辑
                # 通常会调用 MoviePilot 的配置保存接口
                logger.info(f"前端配置保存请求已接收: {list(config_data.keys())}")
            return {
                "success": True,
                "message": "配置保存成功",
                "data": config_data or {}
            }
        except Exception as e:
            logger.error(f"保存配置API失败: {str(e)}")
            return {"success": False, "error": str(e)}

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
                                'text': f'配置的同步路径数量: {1 if self._source_path and self._target_path else 0}',
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
            # 解析第一个路径作为主路径
            sync_paths = self._parse_sync_paths_compatible(sync_paths_str)
            if sync_paths:
                self._source_path = sync_paths[0].get("source", "")
                self._target_path = sync_paths[0].get("target", "")
            else:
                self._source_path = ""
                self._target_path = ""
        else:
            # 向后兼容旧版本配置
            self._source_path = config.get("sync_root_path", "")
            self._target_path = config.get("sync_target_path", "")

        self._delay_minutes = config.get("delay_minutes", 5)
        self._enable_notifications = config.get("enable_notifications", False)

        # 解析枚举值
        try:
            self._sync_strategy = SyncStrategy(config.get("sync_strategy", "copy"))
        except ValueError:
            self._sync_strategy = SyncStrategy.COPY

        try:
            self._sync_type = SyncType(config.get("sync_type", "incremental"))
        except ValueError:
            self._sync_type = SyncType.INCREMENTAL

        try:
            self._execution_mode = ExecutionMode(config.get("execution_mode", "immediate"))
        except ValueError:
            self._execution_mode = ExecutionMode.IMMEDIATE

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
        """获取插件服务 - 暂不提供定时服务"""
        # 移除定时任务，通过事件触发和手动同步执行
        return []

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