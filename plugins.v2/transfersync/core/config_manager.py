"""
配置管理器 - 参考 p115strmhelper 的配置管理设计
"""
from typing import Dict, List, Any, Optional
from pathlib import Path
from app.log import logger
from app.core.cache import cached

from ..sync_types import SyncStrategy, SyncType, ExecutionMode, TriggerEvent
from ..config_validator import ConfigValidator


class ConfigManager:
    """配置管理器"""

    def __init__(self):
        self._validator = ConfigValidator()

    def parse_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """解析和验证配置"""
        parsed_config = {
            # 基础配置
            "enabled": config.get("enabled", False),
            "sync_paths": self._parse_sync_paths(config.get("sync_paths", "")),
            "delay_minutes": config.get("delay_minutes", 5),
            "enable_immediate_execution": config.get("enable_immediate_execution", True),

            # 定时任务配置
            "enable_incremental": config.get("enable_incremental", False),
            "incremental_cron": config.get("incremental_cron", "0 */6 * * *"),
            "enable_full_sync": config.get("enable_full_sync", False),
            "full_sync_cron": config.get("full_sync_cron", "0 2 * * 0"),

            # 通知配置
            "enable_notifications": config.get("enable_notifications", False),
            "notification_channels": self._parse_list(config.get("notification_channels", "")),

            # 高级配置
            "sync_strategy": self._parse_sync_strategy(config.get("sync_strategy", "copy")),
            "execution_mode": self._parse_execution_mode(config.get("execution_mode", "immediate")),
            "sync_type": self._parse_sync_type(config.get("sync_type", "incremental")),
            "max_depth": config.get("max_depth", -1),
            "file_filters": self._parse_list(config.get("file_filters", "")),
            "exclude_patterns": self._parse_list(config.get("exclude_patterns", "")),
            "max_file_size": config.get("max_file_size", 0),
            "min_file_size": config.get("min_file_size", 0),
            "enable_progress": config.get("enable_progress", True),
            "max_workers": config.get("max_workers", 4),
            "batch_size": config.get("batch_size", 100),

            # 事件触发配置
            "trigger_events": self._parse_trigger_events(config.get("trigger_events", [])),
            "event_conditions": self._parse_event_conditions(config.get("event_conditions", ""))
        }

        # 验证配置
        self._validate_config(parsed_config)

        return parsed_config

    def _parse_sync_paths(self, sync_paths_str: str) -> List[Dict[str, str]]:
        """解析同步路径配置
        支持多种格式：
        1. source1->target1,source2->target2
        2. source1::target1,source2::target2
        """
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

    def _parse_list(self, list_str: str, separator: str = ',') -> List[str]:
        """解析列表字符串"""
        if not list_str:
            return []
        return [item.strip() for item in list_str.split(separator) if item.strip()]

    def _parse_sync_strategy(self, strategy_str: str) -> SyncStrategy:
        """解析同步策略"""
        try:
            return SyncStrategy(strategy_str)
        except ValueError:
            logger.warning(f"无效的同步策略: {strategy_str}, 使用默认值 copy")
            return SyncStrategy.COPY

    def _parse_execution_mode(self, mode_str: str) -> ExecutionMode:
        """解析执行模式"""
        try:
            return ExecutionMode(mode_str)
        except ValueError:
            logger.warning(f"无效的执行模式: {mode_str}, 使用默认值 immediate")
            return ExecutionMode.IMMEDIATE

    def _parse_sync_type(self, type_str: str) -> SyncType:
        """解析同步类型"""
        try:
            return SyncType(type_str)
        except ValueError:
            logger.warning(f"无效的同步类型: {type_str}, 使用默认值 incremental")
            return SyncType.INCREMENTAL

    def _parse_trigger_events(self, events_config: Any) -> List[TriggerEvent]:
        """解析触发事件"""
        events = []

        if isinstance(events_config, list):
            # 直接处理列表格式（来自UI）
            for val in events_config:
                if self._is_valid_event(val):
                    try:
                        events.append(TriggerEvent(val))
                    except ValueError:
                        logger.warning(f"无效的触发事件: {val}")
        elif isinstance(events_config, str) and events_config:
            # 处理字符串格式（向后兼容）
            event_values = self._parse_list(events_config)
            for val in event_values:
                if self._is_valid_event(val):
                    try:
                        events.append(TriggerEvent(val))
                    except ValueError:
                        logger.warning(f"无效的触发事件: {val}")

        # 如果没有配置有效事件，使用默认值
        if not events:
            events = [TriggerEvent.TRANSFER_COMPLETE]

        return events

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

    def _is_valid_event(self, event_value: str) -> bool:
        """检查事件值是否有效"""
        if not event_value:
            return False
        try:
            TriggerEvent(event_value)
            return True
        except ValueError:
            return False

    @cached(region="transfersync_config", ttl=300, skip_none=True)
    def _validate_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """验证配置（带缓存）"""
        if self._validator:
            validation_result = self._validator.validate_all_config(config)
            if not validation_result.get('valid', True):
                logger.warning(f"配置验证警告: {validation_result.get('warnings', [])}")
                if validation_result.get('errors'):
                    logger.error(f"配置验证错误: {validation_result.get('errors', [])}")
            return validation_result
        return {"valid": True}