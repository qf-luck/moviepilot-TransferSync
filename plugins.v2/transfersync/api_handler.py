"""
API处理模块 - 处理所有API端点
"""
from typing import Any, Dict, List, Optional
from datetime import datetime
from app.log import logger


class ApiHandler:
    """API端点处理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        
    def get_api(self) -> List[Dict[str, Any]]:
        """获取API端点列表"""
        return [
            {
                "path": "/config",
                "endpoint": self.get_config,
                "methods": ["GET"]
            },
            {
                "path": "/config",
                "endpoint": self.update_config,
                "methods": ["POST"]
            },
            {
                "path": "/sync",
                "endpoint": self.trigger_sync,
                "methods": ["POST"]
            },
            {
                "path": "/status",
                "endpoint": self.get_status,
                "methods": ["GET"]
            },
            {
                "path": "/stats",
                "endpoint": self.get_statistics,
                "methods": ["GET"]
            },
            {
                "path": "/directories",
                "endpoint": self.browse_directories,
                "methods": ["GET"]
            },
            {
                "path": "/health",
                "endpoint": self.get_health,
                "methods": ["GET"]
            },
            {
                "path": "/performance",
                "endpoint": self.get_performance,
                "methods": ["GET"]
            },
            {
                "path": "/backup",
                "endpoint": self.backup_config,
                "methods": ["POST"]
            },
            {
                "path": "/restore",
                "endpoint": self.restore_config,
                "methods": ["POST"]
            },
            {
                "path": "/execute",
                "endpoint": self.execute_immediate_sync,
                "methods": ["POST"]
            }
        ]
    
    def get_config(self) -> Dict[str, Any]:
        """获取当前配置"""
        try:
            return {
                "success": True,
                "data": self.plugin._get_config_dict()
            }
        except Exception as e:
            logger.error(f"获取配置失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def update_config(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """更新配置"""
        try:
            self.plugin.update_config(config)
            return {"success": True, "message": "配置更新成功"}
        except Exception as e:
            logger.error(f"更新配置失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def trigger_sync(self, sync_type: str = "incremental") -> Dict[str, Any]:
        """触发同步"""
        try:
            if sync_type == "full":
                self.plugin._full_sync_job()
            else:
                self.plugin._incremental_sync_job()
            return {"success": True, "message": f"{sync_type}同步已触发"}
        except Exception as e:
            logger.error(f"触发同步失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def get_status(self) -> Dict[str, Any]:
        """获取插件状态"""
        try:
            return {
                "success": True,
                "data": {
                    "enabled": self.plugin._enabled,
                    "last_sync_time": self.plugin._last_sync_time.isoformat() if self.plugin._last_sync_time else None,
                    "sync_strategy": self.plugin._sync_strategy.value,
                    "sync_mode": self.plugin._sync_mode.value,
                    "trigger_events": [event.value for event in self.plugin._trigger_events]
                }
            }
        except Exception as e:
            logger.error(f"获取状态失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def get_statistics(self) -> Dict[str, Any]:
        """获取统计信息"""
        try:
            return {
                "success": True,
                "data": {
                    "event_statistics": self.plugin._event_statistics,
                    "performance_metrics": self.plugin._performance_metrics
                }
            }
        except Exception as e:
            logger.error(f"获取统计失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def browse_directories(self, path: str = "/") -> Dict[str, Any]:
        """浏览目录"""
        try:
            directories = self.plugin._get_directories(path)
            return {
                "success": True,
                "data": {
                    "current_path": path,
                    "directories": directories
                }
            }
        except Exception as e:
            logger.error(f"浏览目录失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def get_health(self) -> Dict[str, Any]:
        """获取健康状态"""
        try:
            health_status = self.plugin.health_checker.check_health() if hasattr(self.plugin, 'health_checker') else self.plugin._health_status
            return {
                "success": True,
                "data": health_status
            }
        except Exception as e:
            logger.error(f"获取健康状态失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def get_performance(self) -> Dict[str, Any]:
        """获取性能指标"""
        try:
            metrics = self.plugin.performance_monitor.get_metrics() if hasattr(self.plugin, 'performance_monitor') else self.plugin._performance_metrics
            return {
                "success": True,
                "data": metrics
            }
        except Exception as e:
            logger.error(f"获取性能指标失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def backup_config(self) -> Dict[str, Any]:
        """备份配置"""
        try:
            if hasattr(self.plugin, 'backup_manager'):
                backup_info = self.plugin.backup_manager.create_backup()
                return {"success": True, "data": backup_info}
            else:
                return {"success": False, "message": "备份功能不可用"}
        except Exception as e:
            logger.error(f"备份配置失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def restore_config(self, backup_id: str) -> Dict[str, Any]:
        """恢复配置"""
        try:
            if hasattr(self.plugin, 'backup_manager'):
                self.plugin.backup_manager.restore_backup(backup_id)
                return {"success": True, "message": "配置恢复成功"}
            else:
                return {"success": False, "message": "恢复功能不可用"}
        except Exception as e:
            logger.error(f"恢复配置失败: {str(e)}")
            return {"success": False, "message": str(e)}

    def execute_immediate_sync(self, sync_path: str = None) -> Dict[str, Any]:
        """立即执行同步"""
        try:
            result = self.plugin.execute_immediate_sync(sync_path)
            return result
        except Exception as e:
            logger.error(f"立即执行同步失败: {str(e)}")
            return {"success": False, "message": str(e)}