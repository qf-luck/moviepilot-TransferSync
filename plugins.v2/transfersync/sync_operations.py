"""
同步操作核心功能
"""
import os
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor

from app.log import logger
from .types import SyncStrategy, SyncMode, SyncStatus
from .exceptions import SyncException, SyncPermissionError, SyncSpaceError
from .file_operations import AtomicFileOperation


class SyncOperations:
    """同步操作核心类"""
    
    def __init__(self, config):
        self.config = config
        self.sync_records = {}
        self.sync_progress = {}
        self.current_status = SyncStatus.IDLE
        self.sync_queue = []
        self.lock = threading.Lock()
    
    def sync_single_item(self, source_path: str):
        """同步单个文件或目录"""
        if not self.config.get('copy_paths'):
            logger.warning("未配置同步目标路径")
            return
        
        try:
            source = Path(source_path)
            if not source.exists():
                logger.warning(f"源路径不存在: {source_path}")
                return

            # 生成任务ID
            task_id = f"sync_{int(time.time())}"
            
            # 获取需要同步的文件列表
            if source.is_file():
                files_to_sync = [source] if self._should_sync_file(source) else []
            else:
                files_to_sync = self._get_filtered_files(source)
            
            if not files_to_sync:
                logger.info(f"没有符合条件的文件需要同步: {source_path}")
                return
                
            total_files = len(files_to_sync)
            self._update_progress(task_id, 0, total_files, SyncStatus.RUNNING.value)
            
            successful_syncs = 0
            
            for target_path in self.config.get('copy_paths', []):
                target = Path(target_path)
                if not target.exists():
                    logger.warning(f"目标路径不存在: {target_path}")
                    continue
                
                try:
                    if source.is_file():
                        # 单文件同步
                        target_full = target / source.name
                        if self._execute_sync_strategy(source, target_full):
                            successful_syncs += 1
                            logger.info(f"文件已同步: {source} -> {target_full}")
                    else:
                        # 目录同步
                        sync_mode = self.config.get('sync_mode', SyncMode.IMMEDIATE)
                        if sync_mode == SyncMode.BATCH:
                            self._sync_directory_batch(source, target, files_to_sync, task_id)
                        elif sync_mode == SyncMode.QUEUE:
                            self._sync_directory_queue(source, target, files_to_sync, task_id)
                        else:
                            self._sync_directory_immediate(source, target, files_to_sync, task_id)
                        successful_syncs += 1
                    
                except Exception as e:
                    logger.error(f"同步到 {target_path} 失败: {str(e)}")
                    
            # 记录同步信息
            if successful_syncs > 0:
                with self.lock:
                    self.sync_records[str(source)] = {
                        'sync_time': datetime.now(),
                        'target_paths': self.config.get('copy_paths', []).copy(),
                        'file_count': total_files,
                        'successful_targets': successful_syncs
                    }
                    
            self._update_progress(task_id, total_files, total_files, SyncStatus.COMPLETED.value)
                    
        except Exception as e:
            logger.error(f"同步单个项目失败: {str(e)}")
            if 'task_id' in locals():
                self._update_progress(task_id, 0, 0, SyncStatus.ERROR.value)

    def _sync_directory_immediate(self, source: Path, target_base: Path, files: List[Path], task_id: str):
        """立即同步目录"""
        with ThreadPoolExecutor(max_workers=self.config.get('max_workers', 4)) as executor:
            futures = []
            for file_path in files:
                relative_path = file_path.relative_to(source)
                target_file = target_base / source.name / relative_path
                future = executor.submit(self._execute_sync_strategy, file_path, target_file)
                futures.append(future)
            
            # 等待所有任务完成
            for future in futures:
                try:
                    future.result()
                except Exception as e:
                    logger.error(f"同步文件失败: {str(e)}")

    def _sync_directory_batch(self, source: Path, target_base: Path, files: List[Path], task_id: str):
        """批量同步目录"""
        batch_size = self.config.get('batch_size', 100)
        for i in range(0, len(files), batch_size):
            batch_files = files[i:i + batch_size]
            self._sync_directory_immediate(source, target_base, batch_files, task_id)
            time.sleep(0.1)  # 短暂休息避免过度占用资源

    def _sync_directory_queue(self, source: Path, target_base: Path, files: List[Path], task_id: str):
        """队列同步目录"""
        with self.lock:
            self.sync_queue.extend(files)
        
        # 逐个处理队列中的文件
        while self.sync_queue:
            with self.lock:
                if not self.sync_queue:
                    break
                file_path = self.sync_queue.pop(0)
            
            try:
                relative_path = file_path.relative_to(source)
                target_file = target_base / source.name / relative_path
                self._execute_sync_strategy(file_path, target_file)
            except Exception as e:
                logger.error(f"队列同步文件失败: {str(e)}")

    def _execute_sync_strategy(self, source: Path, target: Path) -> bool:
        """执行具体的同步策略"""
        if not source.exists():
            logger.warning(f"源文件不存在: {source}")
            return False

        try:
            # 确保目标目录存在
            target.parent.mkdir(parents=True, exist_ok=True)
            
            strategy = self.config.get('sync_strategy', SyncStrategy.COPY)
            
            # 如果目标文件已存在且内容相同，跳过同步
            if target.exists() and source.stat().st_size == target.stat().st_size:
                if source.stat().st_mtime <= target.stat().st_mtime:
                    logger.debug(f"文件已是最新，跳过同步: {target}")
                    return True

            with AtomicFileOperation() as atomic_op:
                if strategy == SyncStrategy.COPY:
                    shutil.copy2(source, target)
                elif strategy == SyncStrategy.MOVE:
                    shutil.move(str(source), str(target))
                elif strategy == SyncStrategy.HARDLINK:
                    if target.exists():
                        target.unlink()
                    target.hardlink_to(source)
                elif strategy == SyncStrategy.SOFTLINK:
                    if target.exists():
                        target.unlink()
                    target.symlink_to(source)
                else:
                    raise ValueError(f"不支持的同步策略: {strategy}")
                
                atomic_op.track_created_file(target)
                logger.debug(f"文件同步完成: {source} -> {target}")
                return True
                
        except PermissionError as e:
            raise SyncPermissionError(f"权限错误: {str(e)}")
        except OSError as e:
            if "No space left on device" in str(e):
                raise SyncSpaceError(f"磁盘空间不足: {str(e)}")
            else:
                raise SyncException(f"文件系统错误: {str(e)}")
        except Exception as e:
            raise SyncException(f"同步失败: {str(e)}")

    def _should_sync_file(self, file_path: Path) -> bool:
        """检查文件是否应该被同步"""
        # 文件大小检查
        file_size_mb = file_path.stat().st_size / (1024 * 1024)
        min_size = self.config.get('min_file_size', 0)
        max_size = self.config.get('max_file_size', 0)
        
        if min_size > 0 and file_size_mb < min_size:
            return False
        if max_size > 0 and file_size_mb > max_size:
            return False
        
        # 文件扩展名检查
        file_filters = self.config.get('file_filters', [])
        if file_filters and file_path.suffix.lower() not in [f.lower() for f in file_filters]:
            return False
        
        # 排除模式检查
        exclude_patterns = self.config.get('exclude_patterns', [])
        for pattern in exclude_patterns:
            if pattern in str(file_path):
                return False
        
        return True

    def _get_filtered_files(self, directory: Path) -> List[Path]:
        """获取过滤后的文件列表"""
        files = []
        max_depth = self.config.get('max_depth', -1)
        
        def collect_files(path: Path, current_depth: int = 0):
            if max_depth >= 0 and current_depth > max_depth:
                return
            
            try:
                for item in path.iterdir():
                    if item.is_file() and self._should_sync_file(item):
                        files.append(item)
                    elif item.is_dir():
                        collect_files(item, current_depth + 1)
            except PermissionError:
                logger.warning(f"无权限访问目录: {path}")
            except Exception as e:
                logger.error(f"扫描目录失败 {path}: {str(e)}")
        
        collect_files(directory)
        return files

    def _update_progress(self, task_id: str, completed: int, total: int, status: str):
        """更新同步进度"""
        with self.lock:
            self.sync_progress[task_id] = {
                'completed': completed,
                'total': total,
                'status': status,
                'timestamp': datetime.now()
            }