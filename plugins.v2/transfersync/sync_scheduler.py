"""
同步调度管理模块
"""
import threading
from typing import Optional
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.log import logger


class SyncScheduler:
    """同步任务调度器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self._scheduler = None
        self._lock = threading.Lock()
        
    def setup_scheduler(self):
        """设置定时任务调度器"""
        try:
            if self._scheduler:
                self._scheduler.shutdown(wait=False)
            
            self._scheduler = BackgroundScheduler(
                timezone=settings.TZ,
                job_defaults={
                    'coalesce': True,
                    'max_instances': 1,
                    'misfire_grace_time': 300
                }
            )
            
            # 添加增量同步任务
            if self.plugin._enable_incremental and self.plugin._incremental_cron:
                try:
                    self._scheduler.add_job(
                        func=self._incremental_sync_job,
                        trigger=CronTrigger.from_crontab(self.plugin._incremental_cron),
                        id="transfersync_incremental",
                        name="TransferSync增量同步",
                        replace_existing=True
                    )
                    logger.info(f"增量同步任务已设置，执行时间: {self.plugin._incremental_cron}")
                except Exception as e:
                    logger.error(f"设置增量同步任务失败: {str(e)}")
            
            # 添加全量同步任务
            if self.plugin._enable_full_sync and self.plugin._full_sync_cron:
                try:
                    self._scheduler.add_job(
                        func=self._full_sync_job,
                        trigger=CronTrigger.from_crontab(self.plugin._full_sync_cron),
                        id="transfersync_full",
                        name="TransferSync全量同步",
                        replace_existing=True
                    )
                    logger.info(f"全量同步任务已设置，执行时间: {self.plugin._full_sync_cron}")
                except Exception as e:
                    logger.error(f"设置全量同步任务失败: {str(e)}")
            
            # 启动调度器
            if not self._scheduler.running:
                self._scheduler.start()
                logger.info("TransferSync调度器已启动")
                
        except Exception as e:
            logger.error(f"设置调度器失败: {str(e)}")
    
    def _incremental_sync_job(self):
        """增量同步任务"""
        with self._lock:
            try:
                logger.info("开始执行增量同步任务")
                start_time = datetime.now()
                
                success_count = 0
                failed_count = 0
                error_message = None
                
                for copy_path in self.plugin._copy_paths:
                    try:
                        result = self.plugin._sync_ops.sync_directory(copy_path, incremental=True)
                        if result.get('success', False):
                            success_count += result.get('success_count', 0)
                            failed_count += result.get('failed_count', 0)
                        else:
                            failed_count += 1
                            error_message = result.get('error_message', '未知错误')
                    except Exception as e:
                        logger.error(f"同步路径 {copy_path} 失败: {str(e)}")
                        failed_count += 1
                        error_message = str(e)
                
                processing_time = (datetime.now() - start_time).total_seconds()
                
                # 更新最后同步时间
                self.plugin._last_sync_time = datetime.now()
                
                # 发送通知
                if hasattr(self.plugin, 'notification_manager'):
                    result_data = {
                        'success': failed_count == 0,
                        'success_count': success_count,
                        'failed_count': failed_count,
                        'processing_time': processing_time,
                        'sync_path': ', '.join(self.plugin._copy_paths),
                        'error_message': error_message
                    }
                    self.plugin.notification_manager.send_sync_notification("增量", result_data)
                
                logger.info(f"增量同步任务完成，成功: {success_count}, 失败: {failed_count}")
                
            except Exception as e:
                logger.error(f"增量同步任务执行失败: {str(e)}")
                if hasattr(self.plugin, 'notification_manager'):
                    self.plugin.notification_manager.send_error_notification("增量同步失败", str(e))
    
    def _full_sync_job(self):
        """全量同步任务"""
        with self._lock:
            try:
                logger.info("开始执行全量同步任务")
                start_time = datetime.now()
                
                success_count = 0
                failed_count = 0
                error_message = None
                
                for copy_path in self.plugin._copy_paths:
                    try:
                        result = self.plugin._sync_ops.sync_directory(copy_path, incremental=False)
                        if result.get('success', False):
                            success_count += result.get('success_count', 0)
                            failed_count += result.get('failed_count', 0)
                        else:
                            failed_count += 1
                            error_message = result.get('error_message', '未知错误')
                    except Exception as e:
                        logger.error(f"同步路径 {copy_path} 失败: {str(e)}")
                        failed_count += 1
                        error_message = str(e)
                
                processing_time = (datetime.now() - start_time).total_seconds()
                
                # 更新最后同步时间
                self.plugin._last_sync_time = datetime.now()
                
                # 发送通知
                if hasattr(self.plugin, 'notification_manager'):
                    result_data = {
                        'success': failed_count == 0,
                        'success_count': success_count,
                        'failed_count': failed_count,
                        'processing_time': processing_time,
                        'sync_path': ', '.join(self.plugin._copy_paths),
                        'error_message': error_message
                    }
                    self.plugin.notification_manager.send_sync_notification("全量", result_data)
                
                logger.info(f"全量同步任务完成，成功: {success_count}, 失败: {failed_count}")
                
            except Exception as e:
                logger.error(f"全量同步任务执行失败: {str(e)}")
                if hasattr(self.plugin, 'notification_manager'):
                    self.plugin.notification_manager.send_error_notification("全量同步失败", str(e))
    
    def shutdown(self):
        """关闭调度器"""
        try:
            if self._scheduler and self._scheduler.running:
                self._scheduler.shutdown(wait=False)
                logger.info("TransferSync调度器已关闭")
        except Exception as e:
            logger.error(f"关闭调度器失败: {str(e)}")
    
    def get_job_status(self) -> dict:
        """获取任务状态"""
        try:
            if not self._scheduler:
                return {"status": "not_initialized", "jobs": []}
            
            jobs_info = []
            for job in self._scheduler.get_jobs():
                jobs_info.append({
                    "id": job.id,
                    "name": job.name,
                    "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
                    "trigger": str(job.trigger)
                })
            
            return {
                "status": "running" if self._scheduler.running else "stopped",
                "jobs": jobs_info
            }
        except Exception as e:
            logger.error(f"获取任务状态失败: {str(e)}")
            return {"status": "error", "message": str(e), "jobs": []}
    
    def trigger_job(self, job_id: str) -> bool:
        """手动触发指定任务"""
        try:
            if not self._scheduler:
                return False
            
            job = self._scheduler.get_job(job_id)
            if job:
                job.func()
                logger.info(f"手动触发任务成功: {job_id}")
                return True
            else:
                logger.error(f"任务不存在: {job_id}")
                return False
        except Exception as e:
            logger.error(f"触发任务失败: {str(e)}")
            return False