"""
服务管理器 - 参考 p115strmhelper 的服务管理设计
"""
from typing import Dict, Any, Optional
from app.log import logger
from app.helper.notification import NotificationHelper
from app.helper.mediaserver import MediaServerHelper
from app.helper.storage import StorageHelper
from app.core.cache import TTLCache


class ServiceManager:
    """服务管理器 - 管理外部服务的依赖"""

    def __init__(self):
        self._notification_helper = None
        self._mediaserver_helper = None
        self._storage_helper = None
        self._local_cache = TTLCache(maxsize=100, ttl=300)  # 5分钟TTL本地缓存
        self._performance_metrics = {}
        self._health_status = {"status": "unknown", "checks": {}}

    @property
    def notification_helper(self) -> NotificationHelper:
        """获取通知助手实例"""
        if self._notification_helper is None:
            self._notification_helper = NotificationHelper()
        return self._notification_helper

    @property
    def mediaserver_helper(self) -> MediaServerHelper:
        """获取媒体服务器助手实例"""
        if self._mediaserver_helper is None:
            self._mediaserver_helper = MediaServerHelper()
        return self._mediaserver_helper

    @property
    def storage_helper(self) -> StorageHelper:
        """获取存储助手实例"""
        if self._storage_helper is None:
            self._storage_helper = StorageHelper()
        return self._storage_helper

    @property
    def local_cache(self) -> TTLCache:
        """获取本地缓存实例"""
        return self._local_cache

    def clear_cache(self):
        """清理缓存"""
        try:
            if self._local_cache:
                self._local_cache.clear()
            logger.info("服务管理器缓存已清理")
        except Exception as e:
            logger.error(f"清理服务管理器缓存失败: {str(e)}")

    def update_performance_metrics(self, metric_name: str, value: Any):
        """更新性能指标"""
        self._performance_metrics[metric_name] = {
            'value': value,
            'timestamp': logger.get_current_time() if hasattr(logger, 'get_current_time') else None
        }

    def get_performance_metrics(self) -> Dict[str, Any]:
        """获取性能指标"""
        return self._performance_metrics.copy()

    def update_health_status(self, component: str, status: str, details: str = ""):
        """更新健康状态"""
        self._health_status["checks"][component] = {
            "status": status,
            "details": details,
            "timestamp": logger.get_current_time() if hasattr(logger, 'get_current_time') else None
        }

        # 更新整体状态
        all_checks = self._health_status["checks"]
        if all(check["status"] == "healthy" for check in all_checks.values()):
            self._health_status["status"] = "healthy"
        elif any(check["status"] == "error" for check in all_checks.values()):
            self._health_status["status"] = "error"
        else:
            self._health_status["status"] = "warning"

    def get_health_status(self) -> Dict[str, Any]:
        """获取健康状态"""
        return self._health_status.copy()

    def shutdown(self):
        """关闭服务管理器"""
        try:
            self.clear_cache()
            self._notification_helper = None
            self._mediaserver_helper = None
            self._storage_helper = None
            logger.info("服务管理器已关闭")
        except Exception as e:
            logger.error(f"关闭服务管理器失败: {str(e)}")