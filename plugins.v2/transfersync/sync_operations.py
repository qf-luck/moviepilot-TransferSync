"""
同步操作核心功能 - 重构版本
参考 p115strmhelper 的操作设计，支持多路径配置
"""
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from app.log import logger
from .sync_types import SyncStrategy, SyncType, ExecutionMode, SyncStatus
from .exceptions import SyncException, SyncPermissionError, SyncSpaceError
from .file_operations import AtomicFileOperation


class SyncOperations:
    """同步操作核心类 - 支持多路径配置"""

    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self.sync_records = {}
        self.sync_progress = {}
        self.current_status = SyncStatus.IDLE
        self.sync_queue = []
        self.lock = threading.Lock()

    def execute_sync(self, sync_config: Dict[str, str], trigger_path: str) -> Dict[str, Any]:
        """
        执行同步操作 - 新的主要入口方法

        Args:
            sync_config: 同步配置，包含 source 和 target
            trigger_path: 触发同步的具体路径

        Returns:
            同步结果字典
        """
        result = {
            'success': False,
            'message': '',
            'synced_files': 0,
            'error_type': None,
            'source_path': sync_config.get('source', ''),
            'target_path': sync_config.get('target', ''),
            'trigger_path': trigger_path
        }

        try:
            source_path = sync_config.get('source', '')
            target_path = sync_config.get('target', '')

            if not source_path or not target_path:
                result['message'] = "同步配置中缺少源路径或目标路径"
                result['error_type'] = "config_error"
                return result

            # 验证路径
            if not self._validate_paths(source_path, target_path, result):
                return result

            # 计算相对路径
            relative_path = self._calculate_relative_path(trigger_path, source_path)
            if relative_path is None:
                result['message'] = f"触发路径 {trigger_path} 不在源路径 {source_path} 范围内"
                result['error_type'] = "path_error"
                return result

            # 执行同步
            return self._perform_sync(source_path, target_path, relative_path, result)

        except Exception as e:
            logger.error(f"执行同步操作失败: {str(e)}")
            result['message'] = f"同步操作异常: {str(e)}"
            result['error_type'] = "sync_error"
            return result

    def sync_directory(self, source_path: str) -> dict:
        """
        向后兼容的同步目录方法
        自动匹配合适的同步配置
        """
        # 查找匹配的同步配置
        sync_configs = self._find_matching_sync_configs(source_path)

        if not sync_configs:
            return {
                'success': False,
                'message': f"路径 {source_path} 没有匹配的同步配置",
                'synced_files': 0,
                'error_type': "config_error"
            }

        # 使用第一个匹配的配置
        sync_config = sync_configs[0]
        return self.execute_sync(sync_config, source_path)

    def _find_matching_sync_configs(self, path: str) -> List[Dict[str, str]]:
        """查找匹配的同步配置"""
        sync_paths = getattr(self.plugin, '_sync_paths', [])
        matching_configs = []

        for sync_config in sync_paths:
            source_path = sync_config.get('source', '')
            if path.startswith(source_path):
                matching_configs.append(sync_config)

        return matching_configs

    def _validate_paths(self, source_path: str, target_path: str, result: Dict[str, Any]) -> bool:
        """验证源路径和目标路径"""
        try:
            source = Path(source_path)
            target = Path(target_path)

            # 检查源路径是否存在
            if not source.exists():
                result['message'] = f"源路径不存在: {source_path}"
                result['error_type'] = "source_not_found"
                return False

            # 检查源路径是否为目录
            if not source.is_dir():
                result['message'] = f"源路径不是目录: {source_path}"
                result['error_type'] = "source_not_directory"
                return False

            # 检查目标路径的父目录是否存在
            if not target.parent.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    logger.info(f"创建目标父目录: {target.parent}")
                except Exception as e:
                    result['message'] = f"无法创建目标父目录: {str(e)}"
                    result['error_type'] = "target_create_error"
                    return False

            return True

        except Exception as e:
            result['message'] = f"路径验证失败: {str(e)}"
            result['error_type'] = "validation_error"
            return False

    def _calculate_relative_path(self, trigger_path: str, source_path: str) -> Optional[str]:
        """计算触发路径相对于源路径的相对路径"""
        try:
            trigger = Path(trigger_path)
            source = Path(source_path)

            # 检查触发路径是否在源路径范围内
            try:
                relative = trigger.relative_to(source)
                return str(relative) if str(relative) != '.' else ''
            except ValueError:
                # 不在范围内
                return None

        except Exception as e:
            logger.error(f"计算相对路径失败: {str(e)}")
            return None

    def _perform_sync(self, source_path: str, target_path: str,
                     relative_path: str, result: Dict[str, Any]) -> Dict[str, Any]:
        """执行实际的同步操作"""
        try:
            # 计算实际的源和目标路径
            if relative_path:
                actual_source = Path(source_path) / relative_path
                actual_target = Path(target_path) / relative_path
            else:
                actual_source = Path(source_path)
                actual_target = Path(target_path)

            # 更新同步状态
            self.current_status = SyncStatus.RUNNING

            # 获取同步策略
            sync_strategy = getattr(self.plugin, '_sync_strategy', SyncStrategy.COPY)

            # 执行同步
            if actual_source.is_file():
                synced_files = self._sync_file(actual_source, actual_target, sync_strategy)
            else:
                synced_files = self._sync_directory_recursive(actual_source, actual_target, sync_strategy)

            result.update({
                'success': True,
                'message': f"同步完成，处理了 {synced_files} 个文件",
                'synced_files': synced_files
            })

            # 发送通知
            self._send_sync_notification(result)

            return result

        except Exception as e:
            logger.error(f"执行同步失败: {str(e)}")
            result.update({
                'success': False,
                'message': f"同步失败: {str(e)}",
                'error_type': "sync_error"
            })
            return result

        finally:
            self.current_status = SyncStatus.IDLE

    def _sync_file(self, source_file: Path, target_file: Path, strategy: SyncStrategy) -> int:
        """同步单个文件"""
        try:
            # 检查文件过滤器
            if not self._should_sync_file(source_file):
                logger.debug(f"文件被过滤器排除: {source_file}")
                return 0

            # 确保目标目录存在
            target_file.parent.mkdir(parents=True, exist_ok=True)

            # 检查是否需要同步
            if target_file.exists() and not self._should_overwrite_file(source_file, target_file):
                logger.debug(f"目标文件已存在且无需更新: {target_file}")
                return 0

            # 执行同步
            if strategy == SyncStrategy.COPY:
                shutil.copy2(source_file, target_file)
                logger.info(f"复制文件: {source_file} -> {target_file}")
            elif strategy == SyncStrategy.MOVE:
                shutil.move(str(source_file), str(target_file))
                logger.info(f"移动文件: {source_file} -> {target_file}")
            elif strategy == SyncStrategy.SOFTLINK:
                if target_file.exists():
                    target_file.unlink()
                target_file.symlink_to(source_file)
                logger.info(f"创建软链接: {source_file} -> {target_file}")
            elif strategy == SyncStrategy.HARDLINK:
                if target_file.exists():
                    target_file.unlink()
                target_file.hardlink_to(source_file)
                logger.info(f"创建硬链接: {source_file} -> {target_file}")

            return 1

        except Exception as e:
            logger.error(f"同步文件失败 {source_file}: {str(e)}")
            raise SyncException(f"同步文件失败: {str(e)}")

    def _sync_directory_recursive(self, source_dir: Path, target_dir: Path,
                                 strategy: SyncStrategy) -> int:
        """递归同步目录"""
        synced_files = 0
        max_workers = getattr(self.plugin, '_max_workers', 4)
        enable_progress = getattr(self.plugin, '_enable_progress', True)

        try:
            # 收集所有需要同步的文件
            files_to_sync = []
            for root, dirs, files in os.walk(source_dir):
                root_path = Path(root)

                # 检查目录深度限制
                if not self._check_depth_limit(source_dir, root_path):
                    continue

                for file in files:
                    source_file = root_path / file
                    relative_path = source_file.relative_to(source_dir)
                    target_file = target_dir / relative_path

                    if self._should_sync_file(source_file):
                        files_to_sync.append((source_file, target_file))

            # 使用线程池进行并发同步
            if files_to_sync:
                with ThreadPoolExecutor(max_workers=max_workers) as executor:
                    futures = []
                    for source_file, target_file in files_to_sync:
                        future = executor.submit(self._sync_file, source_file, target_file, strategy)
                        futures.append(future)

                    # 等待所有任务完成并统计结果
                    for future in futures:
                        try:
                            synced_files += future.result()
                        except Exception as e:
                            logger.error(f"同步任务失败: {str(e)}")

            return synced_files

        except Exception as e:
            logger.error(f"递归同步目录失败: {str(e)}")
            raise SyncException(f"递归同步目录失败: {str(e)}")

    def _should_sync_file(self, file_path: Path) -> bool:
        """检查文件是否应该同步"""
        try:
            # 检查文件大小限制
            file_size_mb = file_path.stat().st_size / (1024 * 1024)
            max_size = getattr(self.plugin, '_max_file_size', 0)
            min_size = getattr(self.plugin, '_min_file_size', 0)

            if max_size > 0 and file_size_mb > max_size:
                return False
            if min_size > 0 and file_size_mb < min_size:
                return False

            # 检查文件过滤器
            file_filters = getattr(self.plugin, '_file_filters', [])
            if file_filters:
                file_ext = file_path.suffix.lower()
                if not any(filter_ext.lower() in file_ext for filter_ext in file_filters):
                    return False

            # 检查排除模式
            exclude_patterns = getattr(self.plugin, '_exclude_patterns', [])
            if exclude_patterns:
                file_name = file_path.name.lower()
                if any(pattern.lower() in file_name for pattern in exclude_patterns):
                    return False

            return True

        except Exception as e:
            logger.error(f"检查文件过滤条件失败: {str(e)}")
            return False

    def _should_overwrite_file(self, source_file: Path, target_file: Path) -> bool:
        """检查是否应该覆盖目标文件"""
        try:
            # 比较文件修改时间
            source_mtime = source_file.stat().st_mtime
            target_mtime = target_file.stat().st_mtime

            # 如果源文件更新，则覆盖
            return source_mtime > target_mtime

        except Exception as e:
            logger.error(f"比较文件时间失败: {str(e)}")
            return True  # 出错时选择覆盖

    def _check_depth_limit(self, base_path: Path, current_path: Path) -> bool:
        """检查目录深度限制"""
        try:
            max_depth = getattr(self.plugin, '_max_depth', -1)
            if max_depth <= 0:
                return True

            # 计算当前深度
            try:
                relative_path = current_path.relative_to(base_path)
                current_depth = len(relative_path.parts)
                return current_depth <= max_depth
            except ValueError:
                return False

        except Exception as e:
            logger.error(f"检查深度限制失败: {str(e)}")
            return True

    def _send_sync_notification(self, result: Dict[str, Any]):
        """发送同步通知"""
        try:
            enable_notifications = getattr(self.plugin, '_enable_notifications', False)
            if not enable_notifications:
                return

            # 通过服务管理器发送通知
            if hasattr(self.plugin, '_service_manager'):
                notification_helper = self.plugin._service_manager.notification_helper

                title = "同步完成" if result['success'] else "同步失败"
                message = result['message']

                # 这里可以调用 notification_helper 发送通知
                logger.info(f"同步通知: {title} - {message}")

        except Exception as e:
            logger.error(f"发送同步通知失败: {str(e)}")

    def get_sync_status(self) -> Dict[str, Any]:
        """获取当前同步状态"""
        return {
            'status': self.current_status.value,
            'queue_size': len(self.sync_queue),
            'progress': self.sync_progress.copy(),
            'records': list(self.sync_records.values())[-10:]  # 最近10条记录
        }