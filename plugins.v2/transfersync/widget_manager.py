"""
仪表板组件管理模块
"""
from typing import Dict, Any, List
from datetime import datetime
from app.log import logger


class WidgetManager:
    """仪表板组件管理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
    
    def get_dashboard_widget(self) -> Dict[str, Any]:
        """获取仪表板组件配置"""
        return {
            "id": "transfersync_widget",
            "name": "TransferSync 状态",
            "component": "TransferSyncWidget",
            "props": {
                "refresh_interval": 30000,  # 30秒刷新间隔
                "show_charts": True,
                "compact_mode": False
            },
            "grid": {
                "cols": 12,
                "rows": 4,
                "minW": 6,
                "minH": 3
            }
        }
    
    def get_widget_data(self) -> Dict[str, Any]:
        """获取组件数据"""
        try:
            # 基础状态信息
            widget_data = {
                "status": {
                    "enabled": self.plugin._enabled,
                    "last_sync": self.plugin._last_sync_time.isoformat() if self.plugin._last_sync_time else None,
                    "sync_strategy": self.plugin._sync_strategy.value,
                    "sync_mode": self.plugin._sync_mode.value
                },
                "paths": {
                    "total": len(self.plugin._copy_paths),
                    "configured": [path for path in self.plugin._copy_paths if path]
                },
                "events": {
                    "listening": [event.value for event in self.plugin._trigger_events],
                    "count": len(self.plugin._trigger_events)
                }
            }
            
            # 统计信息
            total_events = 0
            total_success = 0
            recent_errors = []
            
            for event_type, stats in self.plugin._event_statistics.items():
                total_events += stats.get('total_count', 0)
                total_success += stats.get('success_count', 0)
                
                # 收集最近的错误
                if 'recent_errors' in stats:
                    recent_errors.extend(stats['recent_errors'][-3:])  # 最近3个错误
            
            widget_data["statistics"] = {
                "total_events": total_events,
                "success_events": total_success,
                "success_rate": round((total_success / total_events * 100) if total_events > 0 else 0, 2),
                "recent_errors": recent_errors[-5:]  # 最近5个错误
            }
            
            # 调度器状态
            if hasattr(self.plugin, 'sync_scheduler'):
                job_status = self.plugin.sync_scheduler.get_job_status()
                widget_data["scheduler"] = {
                    "status": job_status.get("status", "unknown"),
                    "jobs": job_status.get("jobs", [])
                }
            
            # 性能指标
            widget_data["performance"] = self.plugin._performance_metrics
            
            # 健康状态
            if hasattr(self.plugin, 'health_checker'):
                health = self.plugin.health_checker.check_health()
                widget_data["health"] = health
            else:
                widget_data["health"] = self.plugin._health_status
            
            return widget_data
            
        except Exception as e:
            logger.error(f"获取组件数据失败: {str(e)}")
            return {
                "error": str(e),
                "status": {"enabled": False},
                "statistics": {"total_events": 0, "success_rate": 0}
            }
    
    def get_chart_data(self, chart_type: str = "events", period: str = "24h") -> Dict[str, Any]:
        """获取图表数据"""
        try:
            if chart_type == "events":
                return self._get_events_chart_data(period)
            elif chart_type == "performance":
                return self._get_performance_chart_data(period)
            elif chart_type == "sync_status":
                return self._get_sync_status_chart_data(period)
            else:
                return {"error": f"不支持的图表类型: {chart_type}"}
                
        except Exception as e:
            logger.error(f"获取图表数据失败: {str(e)}")
            return {"error": str(e)}
    
    def _get_events_chart_data(self, period: str) -> Dict[str, Any]:
        """获取事件统计图表数据"""
        labels = []
        success_data = []
        failed_data = []
        
        for event_type, stats in self.plugin._event_statistics.items():
            labels.append(self.plugin._get_event_display_name(event_type))
            success_data.append(stats.get('success_count', 0))
            failed_data.append(stats.get('failed_count', 0))
        
        return {
            "type": "bar",
            "data": {
                "labels": labels,
                "datasets": [
                    {
                        "label": "成功",
                        "data": success_data,
                        "backgroundColor": "#4CAF50",
                        "borderColor": "#4CAF50",
                        "borderWidth": 1
                    },
                    {
                        "label": "失败",
                        "data": failed_data,
                        "backgroundColor": "#f44336",
                        "borderColor": "#f44336",
                        "borderWidth": 1
                    }
                ]
            },
            "options": {
                "responsive": True,
                "scales": {
                    "y": {
                        "beginAtZero": True
                    }
                }
            }
        }
    
    def _get_performance_chart_data(self, period: str) -> Dict[str, Any]:
        """获取性能指标图表数据"""
        metrics = self.plugin._performance_metrics
        
        labels = ["CPU使用率", "内存使用率", "磁盘使用率", "网络延迟"]
        data = [
            metrics.get('cpu_usage', 0),
            metrics.get('memory_usage', 0),
            metrics.get('disk_usage', 0),
            metrics.get('network_latency', 0)
        ]
        
        return {
            "type": "doughnut",
            "data": {
                "labels": labels,
                "datasets": [{
                    "data": data,
                    "backgroundColor": [
                        "#FF6384",
                        "#36A2EB", 
                        "#FFCE56",
                        "#4BC0C0"
                    ],
                    "borderWidth": 1
                }]
            },
            "options": {
                "responsive": True,
                "maintainAspectRatio": False
            }
        }
    
    def _get_sync_status_chart_data(self, period: str) -> Dict[str, Any]:
        """获取同步状态图表数据"""
        # 统计各种同步状态
        status_counts = {"成功": 0, "失败": 0, "跳过": 0, "进行中": 0}
        
        for stats in self.plugin._event_statistics.values():
            status_counts["成功"] += stats.get('success_count', 0)
            status_counts["失败"] += stats.get('failed_count', 0)
            
        return {
            "type": "pie",
            "data": {
                "labels": list(status_counts.keys()),
                "datasets": [{
                    "data": list(status_counts.values()),
                    "backgroundColor": [
                        "#4CAF50",  # 成功 - 绿色
                        "#f44336",  # 失败 - 红色
                        "#FF9800",  # 跳过 - 橙色
                        "#2196F3"   # 进行中 - 蓝色
                    ],
                    "borderWidth": 1
                }]
            },
            "options": {
                "responsive": True,
                "plugins": {
                    "legend": {
                        "position": "bottom"
                    }
                }
            }
        }
    
    def get_quick_actions(self) -> List[Dict[str, Any]]:
        """获取快速操作按钮"""
        actions = [
            {
                "id": "trigger_incremental_sync",
                "title": "增量同步",
                "icon": "mdi-sync",
                "color": "primary",
                "action": "trigger_sync",
                "params": {"sync_type": "incremental"}
            },
            {
                "id": "trigger_full_sync", 
                "title": "全量同步",
                "icon": "mdi-sync-alert",
                "color": "warning",
                "action": "trigger_sync",
                "params": {"sync_type": "full"}
            },
            {
                "id": "test_notification",
                "title": "测试通知",
                "icon": "mdi-bell-ring",
                "color": "info",
                "action": "test_notification"
            }
        ]
        
        # 根据插件状态调整按钮可用性
        if not self.plugin._enabled:
            for action in actions:
                action["disabled"] = True
                action["tooltip"] = "插件未启用"
        
        return actions
    
    def handle_widget_action(self, action: str, params: Dict[str, Any] = None) -> Dict[str, Any]:
        """处理组件操作"""
        try:
            if hasattr(self.plugin, 'command_handler'):
                return self.plugin.command_handler.handle_command(action, **(params or {}))
            else:
                # 兼容处理
                if action == "trigger_sync":
                    sync_type = params.get("sync_type", "incremental") if params else "incremental"
                    if sync_type == "full":
                        self.plugin._full_sync_job()
                    else:
                        self.plugin._incremental_sync_job()
                    return {"success": True, "message": f"{sync_type}同步已触发"}
                elif action == "test_notification":
                    self.plugin._send_notification("测试通知", "这是一条测试通知")
                    return {"success": True, "message": "测试通知已发送"}
                else:
                    return {"success": False, "message": f"未知操作: {action}"}
        except Exception as e:
            logger.error(f"处理组件操作失败: {str(e)}")
            return {"success": False, "message": str(e)}