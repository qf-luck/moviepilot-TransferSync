"""
命令处理模块
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from app.core.event import eventmanager, Event, EventType
from app.log import logger


class CommandHandler:
    """命令处理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
    
    def get_command(self) -> List[Dict[str, Any]]:
        """获取插件命令列表"""
        return [
            {
                "cmd": "/transfersync_status",
                "event": EventType.PluginAction,
                "desc": "查看TransferSync状态",
                "category": "TransferSync",
                "data": {
                    "action": "get_status"
                }
            },
            {
                "cmd": "/transfersync_sync",
                "event": EventType.PluginAction,
                "desc": "立即执行同步",
                "category": "TransferSync",
                "data": {
                    "action": "trigger_sync"
                }
            },
            {
                "cmd": "/transfersync_stats",
                "event": EventType.PluginAction,
                "desc": "查看统计信息",
                "category": "TransferSync",
                "data": {
                    "action": "get_stats"
                }
            },
            {
                "cmd": "/transfersync_test",
                "event": EventType.PluginAction,
                "desc": "测试通知功能",
                "category": "TransferSync",
                "data": {
                    "action": "test_notification"
                }
            }
        ]
    
    def handle_command(self, action: str, **kwargs) -> Dict[str, Any]:
        """处理命令"""
        try:
            if action == "get_status":
                return self._handle_get_status()
            elif action == "trigger_sync":
                return self._handle_trigger_sync(**kwargs)
            elif action == "get_stats":
                return self._handle_get_stats()
            elif action == "test_notification":
                return self._handle_test_notification()
            else:
                return {"success": False, "message": f"未知命令: {action}"}
        except Exception as e:
            logger.error(f"处理命令失败: {str(e)}")
            return {"success": False, "message": str(e)}
    
    def _handle_get_status(self) -> Dict[str, Any]:
        """获取状态命令处理"""
        try:
            status_info = {
                "插件状态": "启用" if self.plugin._enabled else "禁用",
                "上次同步时间": (
                    self.plugin._last_sync_time.strftime("%Y-%m-%d %H:%M:%S") 
                    if self.plugin._last_sync_time else "从未同步"
                ),
                "同步策略": self.plugin._sync_strategy.value,
                "同步模式": self.plugin._sync_mode.value,
                "监听事件": [event.value for event in self.plugin._trigger_events],
                "同步路径数量": len(self.plugin._copy_paths)
            }
            
            # 获取调度器状态
            if hasattr(self.plugin, 'sync_scheduler'):
                job_status = self.plugin.sync_scheduler.get_job_status()
                status_info["调度器状态"] = job_status.get("status", "未知")
                status_info["定时任务数量"] = len(job_status.get("jobs", []))
            
            return {
                "success": True,
                "message": "状态获取成功",
                "data": status_info
            }
        except Exception as e:
            return {"success": False, "message": f"获取状态失败: {str(e)}"}
    
    def _handle_trigger_sync(self, sync_type: str = "incremental") -> Dict[str, Any]:
        """触发同步命令处理"""
        try:
            if not self.plugin._enabled:
                return {"success": False, "message": "插件未启用"}
            
            if not self.plugin._copy_paths:
                return {"success": False, "message": "未配置同步路径"}
            
            if sync_type == "full":
                if hasattr(self.plugin, 'sync_scheduler'):
                    self.plugin.sync_scheduler._full_sync_job()
                else:
                    self.plugin._full_sync_job()
                message = "全量同步任务已触发"
            else:
                if hasattr(self.plugin, 'sync_scheduler'):
                    self.plugin.sync_scheduler._incremental_sync_job()
                else:
                    self.plugin._incremental_sync_job()
                message = "增量同步任务已触发"
            
            return {"success": True, "message": message}
        except Exception as e:
            return {"success": False, "message": f"触发同步失败: {str(e)}"}
    
    def _handle_get_stats(self) -> Dict[str, Any]:
        """获取统计信息命令处理"""
        try:
            stats_info = {
                "事件统计": self.plugin._event_statistics,
                "性能指标": self.plugin._performance_metrics
            }
            
            # 计算总体统计
            total_events = sum(
                stats.get('total_count', 0) 
                for stats in self.plugin._event_statistics.values()
            )
            total_success = sum(
                stats.get('success_count', 0) 
                for stats in self.plugin._event_statistics.values()
            )
            success_rate = (total_success / total_events * 100) if total_events > 0 else 0
            
            stats_info["总体统计"] = {
                "总事件数": total_events,
                "成功事件数": total_success,
                "成功率": f"{success_rate:.2f}%"
            }
            
            return {
                "success": True,
                "message": "统计信息获取成功",
                "data": stats_info
            }
        except Exception as e:
            return {"success": False, "message": f"获取统计失败: {str(e)}"}
    
    def _handle_test_notification(self) -> Dict[str, Any]:
        """测试通知命令处理"""
        try:
            if not self.plugin._enable_notifications:
                return {"success": False, "message": "通知功能未启用"}
            
            success = False
            if hasattr(self.plugin, 'notification_manager'):
                success = self.plugin.notification_manager.test_notification()
            else:
                # 兼容旧版本方法
                test_title = "TransferSync 测试通知"
                test_message = f"这是一条测试通知，发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
                self.plugin._send_notification(test_title, test_message)
                success = True
            
            if success:
                return {"success": True, "message": "测试通知已发送"}
            else:
                return {"success": False, "message": "测试通知发送失败"}
        except Exception as e:
            return {"success": False, "message": f"测试通知失败: {str(e)}"}
    
    def create_interactive_buttons(self, context: str = "main") -> List[Dict[str, Any]]:
        """创建交互式按钮"""
        buttons = []
        
        if context == "main":
            buttons = [
                {
                    "title": "📊 查看状态",
                    "cmd": "/transfersync_status"
                },
                {
                    "title": "🔄 立即同步",
                    "cmd": "/transfersync_sync"
                },
                {
                    "title": "📈 统计信息",
                    "cmd": "/transfersync_stats"
                },
                {
                    "title": "🔔 测试通知",
                    "cmd": "/transfersync_test"
                }
            ]
        elif context == "sync":
            buttons = [
                {
                    "title": "增量同步",
                    "cmd": "/transfersync_sync",
                    "data": {"sync_type": "incremental"}
                },
                {
                    "title": "全量同步",
                    "cmd": "/transfersync_sync",
                    "data": {"sync_type": "full"}
                }
            ]
        
        return buttons
    
    def format_command_response(self, result: Dict[str, Any], context: str = None) -> str:
        """格式化命令响应"""
        try:
            if not result.get("success", False):
                return f"❌ {result.get('message', '未知错误')}"
            
            message = f"✅ {result.get('message', '操作成功')}"
            
            # 添加详细数据
            if result.get("data"):
                data = result["data"]
                if isinstance(data, dict):
                    message += "\n\n详细信息:"
                    for key, value in data.items():
                        if isinstance(value, (list, dict)):
                            message += f"\n• {key}: {len(value) if isinstance(value, list) else '复杂对象'}"
                        else:
                            message += f"\n• {key}: {value}"
            
            return message
        except Exception as e:
            return f"❌ 响应格式化失败: {str(e)}"