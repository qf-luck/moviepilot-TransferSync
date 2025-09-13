"""
事件管理器 - 参考 p115strmhelper 的事件处理设计
"""
from typing import Dict, Any, List, Optional, Callable
from datetime import datetime, timedelta
import threading
from app.core.event import eventmanager, Event, EventType
from app.log import logger

from ..sync_types import TriggerEvent


class EventManager:
    """事件管理器"""

    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self._registered_events = []
        self._event_statistics = {}
        self._last_event_time = {}
        self._pending_syncs = {}
        self._lock = threading.Lock()

    def register_event_listeners(self, trigger_events: List[TriggerEvent]):
        """注册事件监听器"""
        try:
            self._unregister_all_events()  # 先清理旧的监听器

            for trigger_event in trigger_events:
                self._register_single_event(trigger_event)

            logger.info(f"已注册 {len(trigger_events)} 个事件监听器")
        except Exception as e:
            logger.error(f"注册事件监听器失败: {str(e)}")

    def _register_single_event(self, trigger_event: TriggerEvent):
        """注册单个事件监听器"""
        event_handlers = {
            TriggerEvent.TRANSFER_COMPLETE: (EventType.TransferComplete, self._on_transfer_complete),
            TriggerEvent.DOWNLOAD_ADDED: (EventType.DownloadAdded, self._on_download_added),
            TriggerEvent.SUBSCRIBE_COMPLETE: (EventType.SubscribeComplete, self._on_subscribe_complete),
            TriggerEvent.MEDIA_ADDED: (EventType.MediaAdded, self._on_media_added),
            TriggerEvent.FILE_MOVED: (EventType.FileMoved, self._on_file_moved),
            TriggerEvent.DIRECTORY_SCAN_COMPLETE: (EventType.DirectoryScanComplete, self._on_directory_scan_complete),
            TriggerEvent.SCRAPE_COMPLETE: (EventType.ScrapeComplete, self._on_scrape_complete),
            TriggerEvent.PLUGIN_TRIGGERED: (EventType.PluginAction, self._on_plugin_triggered),
        }

        if trigger_event in event_handlers:
            event_type, handler = event_handlers[trigger_event]
            eventmanager.register(event_type, handler)
            self._registered_events.append((event_type, handler))
            logger.debug(f"已注册事件监听器: {trigger_event.value}")
        else:
            logger.warning(f"未知的触发事件类型: {trigger_event}")

    def unregister_event_listeners(self):
        """取消注册事件监听器"""
        self._unregister_all_events()

    def _unregister_all_events(self):
        """取消注册所有事件监听器"""
        try:
            for event_type, handler in self._registered_events:
                eventmanager.unregister(event_type, handler)
            self._registered_events.clear()
            logger.debug("已取消注册所有事件监听器")
        except Exception as e:
            logger.error(f"取消注册事件监听器失败: {str(e)}")

    def _on_transfer_complete(self, event: Event):
        """整理完成事件处理"""
        self._handle_event("TransferComplete", event)

    def _on_download_added(self, event: Event):
        """下载添加事件处理"""
        self._handle_event("DownloadAdded", event)

    def _on_subscribe_complete(self, event: Event):
        """订阅完成事件处理"""
        self._handle_event("SubscribeComplete", event)

    def _on_media_added(self, event: Event):
        """媒体添加事件处理"""
        self._handle_event("MediaAdded", event)

    def _on_file_moved(self, event: Event):
        """文件移动事件处理"""
        self._handle_event("FileMoved", event)

    def _on_directory_scan_complete(self, event: Event):
        """目录扫描完成事件处理"""
        self._handle_event("DirectoryScanComplete", event)

    def _on_scrape_complete(self, event: Event):
        """刮削完成事件处理"""
        self._handle_event("ScrapeComplete", event)

    def _on_plugin_triggered(self, event: Event):
        """插件触发事件处理"""
        self._handle_event("PluginTriggered", event)

    def _handle_event(self, event_name: str, event: Event):
        """统一事件处理逻辑"""
        try:
            with self._lock:
                # 更新事件统计
                self._update_event_statistics(event_name)

                # 记录事件时间
                current_time = datetime.now()
                event_data = event.event_data if hasattr(event, 'event_data') else {}

                # 提取路径信息
                paths = self._extract_paths_from_event(event_data)

                if not paths:
                    logger.debug(f"事件 {event_name} 未包含有效路径信息")
                    return

                # 检查事件条件
                if not self._check_event_conditions(event_name, event_data):
                    logger.debug(f"事件 {event_name} 不满足过滤条件")
                    return

                # 处理每个路径
                for path in paths:
                    self._process_event_path(event_name, path, current_time)

        except Exception as e:
            logger.error(f"处理事件 {event_name} 失败: {str(e)}")

    def _extract_paths_from_event(self, event_data: Dict[str, Any]) -> List[str]:
        """从事件数据中提取路径信息"""
        paths = []

        # 常见的路径字段名
        path_fields = ['path', 'src', 'dest', 'filepath', 'dir_path', 'transfer_info']

        for field in path_fields:
            if field in event_data:
                value = event_data[field]
                if isinstance(value, str):
                    paths.append(value)
                elif isinstance(value, dict) and 'path' in value:
                    paths.append(value['path'])
                elif hasattr(value, 'path'):
                    paths.append(str(value.path))

        return [p for p in paths if p]  # 过滤空值

    def _check_event_conditions(self, event_name: str, event_data: Dict[str, Any]) -> bool:
        """检查事件是否满足条件"""
        conditions = getattr(self.plugin, '_event_conditions', {})
        if not conditions:
            return True

        # 这里可以根据具体的条件逻辑进行检查
        # 例如：路径匹配、文件类型过滤等
        return True

    def _process_event_path(self, event_name: str, path: str, event_time: datetime):
        """处理事件路径"""
        try:
            # 找到匹配的同步配置
            sync_configs = self._find_matching_sync_configs(path)

            if not sync_configs:
                logger.debug(f"路径 {path} 没有匹配的同步配置")
                return

            # 检查是否需要延迟执行
            delay_minutes = getattr(self.plugin, '_delay_minutes', 5)
            enable_immediate = getattr(self.plugin, '_enable_immediate_execution', True)

            for sync_config in sync_configs:
                if enable_immediate and delay_minutes <= 0:
                    # 立即执行
                    self._execute_sync(sync_config, path)
                else:
                    # 延迟执行
                    self._schedule_delayed_sync(sync_config, path, event_time, delay_minutes)

        except Exception as e:
            logger.error(f"处理事件路径 {path} 失败: {str(e)}")

    def _find_matching_sync_configs(self, path: str) -> List[Dict[str, str]]:
        """查找匹配的同步配置"""
        sync_paths = getattr(self.plugin, '_sync_paths', [])
        matching_configs = []

        for sync_config in sync_paths:
            source_path = sync_config.get('source', '')
            if path.startswith(source_path):
                matching_configs.append(sync_config)

        return matching_configs

    def _schedule_delayed_sync(self, sync_config: Dict[str, str], trigger_path: str,
                              event_time: datetime, delay_minutes: int):
        """安排延迟同步"""
        source_path = sync_config['source']
        target_path = sync_config['target']

        # 使用源路径作为键来管理延迟任务
        pending_key = f"{source_path}->{target_path}"

        # 计算执行时间
        execute_time = event_time + timedelta(minutes=delay_minutes)

        # 更新待执行任务
        self._pending_syncs[pending_key] = {
            'sync_config': sync_config,
            'trigger_path': trigger_path,
            'execute_time': execute_time,
            'event_time': event_time
        }

        logger.info(f"已安排延迟同步任务: {pending_key}, 执行时间: {execute_time}")

        # 启动延迟执行定时器
        self._start_delayed_execution_timer(pending_key, delay_minutes * 60)

    def _start_delayed_execution_timer(self, pending_key: str, delay_seconds: int):
        """启动延迟执行定时器"""
        def execute_delayed_sync():
            try:
                if pending_key in self._pending_syncs:
                    sync_info = self._pending_syncs.pop(pending_key)
                    self._execute_sync(sync_info['sync_config'], sync_info['trigger_path'])
            except Exception as e:
                logger.error(f"执行延迟同步任务失败: {str(e)}")

        timer = threading.Timer(delay_seconds, execute_delayed_sync)
        timer.daemon = True
        timer.start()

    def _execute_sync(self, sync_config: Dict[str, str], trigger_path: str):
        """执行同步操作"""
        try:
            if hasattr(self.plugin, '_sync_ops') and self.plugin._sync_ops:
                self.plugin._sync_ops.execute_sync(sync_config, trigger_path)
            else:
                logger.error("同步操作实例未初始化")
        except Exception as e:
            logger.error(f"执行同步操作失败: {str(e)}")

    def _update_event_statistics(self, event_name: str):
        """更新事件统计"""
        if event_name not in self._event_statistics:
            self._event_statistics[event_name] = {
                'count': 0,
                'last_time': None,
                'first_time': None
            }

        stats = self._event_statistics[event_name]
        stats['count'] += 1
        current_time = datetime.now()
        stats['last_time'] = current_time

        if stats['first_time'] is None:
            stats['first_time'] = current_time

    def get_event_statistics(self) -> Dict[str, Any]:
        """获取事件统计信息"""
        return self._event_statistics.copy()

    def get_pending_syncs(self) -> Dict[str, Any]:
        """获取待执行的同步任务"""
        return self._pending_syncs.copy()