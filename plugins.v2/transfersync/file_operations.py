"""
文件操作相关功能
"""
from pathlib import Path
from typing import List
from app.log import logger


class AtomicFileOperation:
    """原子性文件操作管理器"""
    def __init__(self):
        self.backup_files = []
        self.created_files = []
        self.created_dirs = []
    
    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is not None:
            # 发生异常，执行回滚
            self._rollback()
        return False
    
    def _rollback(self):
        """回滚操作"""
        logger.info("执行原子操作回滚")
        
        # 删除创建的文件
        for file_path in reversed(self.created_files):
            try:
                if file_path.exists():
                    file_path.unlink()
                    logger.debug(f"回滚删除文件: {file_path}")
            except Exception as e:
                logger.warning(f"回滚删除文件失败 {file_path}: {str(e)}")
        
        # 删除创建的目录
        for dir_path in reversed(self.created_dirs):
            try:
                if dir_path.exists() and not any(dir_path.iterdir()):
                    dir_path.rmdir()
                    logger.debug(f"回滚删除目录: {dir_path}")
            except Exception as e:
                logger.warning(f"回滚删除目录失败 {dir_path}: {str(e)}")
    
    def track_created_file(self, file_path: Path):
        """跟踪创建的文件"""
        self.created_files.append(file_path)
    
    def track_created_dir(self, dir_path: Path):
        """跟踪创建的目录"""
        self.created_dirs.append(dir_path)