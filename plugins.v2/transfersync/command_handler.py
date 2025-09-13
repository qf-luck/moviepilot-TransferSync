"""
命令处理模块 - 支持交互式按钮
"""
from typing import Optional, Dict, Any, List
from datetime import datetime
from app.core.event import eventmanager, Event, EventType
from app.log import logger


class CommandHandler:
    """命令处理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self._register_message_action_handler()
    
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
            },
            {
                "cmd": "/transfersync_interactive",
                "event": EventType.PluginAction,
                "desc": "打开交互式控制面板",
                "category": "TransferSync",
                "data": {
                    "action": "show_interactive_menu"
                }
            },
            {
                "cmd": "/transfersync_health",
                "event": EventType.PluginAction,
                "desc": "执行健康检查",
                "category": "TransferSync",
                "data": {
                    "action": "health_check"
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
            elif action == "show_interactive_menu":
                return self._handle_show_interactive_menu(**kwargs)
            elif action == "health_check":
                return self._handle_health_check()
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
                "根路径": self.plugin._sync_root_path or "未设置",
                "目标路径": self.plugin._sync_target_path or "未设置"
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
            
            if not self.plugin._sync_root_path or not self.plugin._sync_target_path:
                return {"success": False, "message": "未配置同步路径"}
            
            if sync_type == "immediate":
                # 立即执行同步
                result = self.plugin.execute_immediate_sync()
                return result
            elif sync_type == "full":
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
    
    def _handle_show_interactive_menu(self, **kwargs) -> Dict[str, Any]:
        """显示交互式菜单"""
        try:
            # 从kwargs中获取消息相关参数
            channel = kwargs.get("channel")
            source = kwargs.get("source") 
            userid = kwargs.get("userid")
            
            if not channel:
                return {"success": False, "message": "缺少必要的消息渠道信息"}
            
            # 发送主菜单
            self._send_main_menu(channel, source, userid)
            
            return {
                "success": True, 
                "message": "交互式控制面板已打开，请在聊天界面中查看"
            }
        except Exception as e:
            logger.error(f"显示交互式菜单失败: {str(e)}")
            return {"success": False, "message": f"显示菜单失败: {str(e)}"}

    def _handle_health_check(self) -> Dict[str, Any]:
        """执行健康检查"""
        try:
            # 执行健康检查
            health_result = self.plugin.health_checker.perform_health_check()
            
            # 格式化结果
            status_emoji = {
                'healthy': '✅',
                'degraded': '⚠️',
                'unhealthy': '❌',
                'error': '💥'
            }
            
            overall_status = health_result.get('overall_status', 'unknown')
            emoji = status_emoji.get(overall_status, '❓')
            
            # 构建简要报告
            summary = f"{emoji} 整体状态: {overall_status}\n"
            
            # 检查项摘要
            checks = health_result.get('checks', {})
            for check_name, check_result in checks.items():
                check_status = '✅' if check_result.get('status') else '❌'
                summary += f"{check_status} {check_name}\n"
            
            # 错误和警告
            errors = health_result.get('errors', [])
            warnings = health_result.get('warnings', [])
            
            if errors:
                summary += f"\n❌ 错误 ({len(errors)}):\n"
                for error in errors[:3]:  # 只显示前3个错误
                    summary += f"• {error}\n"
                if len(errors) > 3:
                    summary += f"• ... 还有 {len(errors) - 3} 个错误\n"
            
            if warnings:
                summary += f"\n⚠️ 警告 ({len(warnings)}):\n"
                for warning in warnings[:3]:  # 只显示前3个警告
                    summary += f"• {warning}\n"
                if len(warnings) > 3:
                    summary += f"• ... 还有 {len(warnings) - 3} 个警告\n"
            
            # 性能信息
            performance = health_result.get('performance', {})
            if performance:
                summary += f"\n📊 性能指标:\n"
                event_perf = performance.get('event_processing', {})
                if event_perf:
                    summary += f"• 事件成功率: {event_perf.get('success_rate', 0)}%\n"
                    summary += f"• 平均处理时间: {event_perf.get('avg_processing_time', 0)}秒\n"
            
            check_duration = health_result.get('check_duration', 0)
            summary += f"\n⏱️ 检查耗时: {check_duration:.2f}秒"
            
            return {
                "success": True,
                "message": "健康检查完成",
                "data": {
                    "overall_status": overall_status,
                    "summary": summary,
                    "full_report": health_result
                }
            }
            
        except Exception as e:
            logger.error(f"健康检查失败: {str(e)}")
            return {"success": False, "message": f"健康检查失败: {str(e)}"}

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

    def _register_message_action_handler(self):
        """注册消息动作处理器"""
        try:
            eventmanager.register(EventType.MessageAction, self._handle_message_action)
            logger.info("TransferSync消息动作处理器已注册")
        except Exception as e:
            logger.error(f"注册消息动作处理器失败: {str(e)}")

    def _handle_message_action(self, event: Event):
        """处理消息按钮回调"""
        try:
            event_data = event.event_data
            if not event_data:
                return

            # 检查是否为本插件的回调
            plugin_id = event_data.get("plugin_id")
            if plugin_id != self.plugin.__class__.__name__:
                return

            # 获取回调数据
            text = event_data.get("text", "")
            channel = event_data.get("channel")
            source = event_data.get("source")
            userid = event_data.get("userid")
            original_message_id = event_data.get("original_message_id")
            original_chat_id = event_data.get("original_chat_id")

            logger.info(f"收到TransferSync交互回调: {text}")

            # 处理不同的交互回调
            if text == "main_menu":
                self._send_main_menu(channel, source, userid, original_message_id, original_chat_id)
            elif text == "status":
                self._send_status_info(channel, source, userid, original_message_id, original_chat_id)
            elif text == "sync_menu":
                self._send_sync_menu(channel, source, userid, original_message_id, original_chat_id)
            elif text == "sync_immediate":
                self._handle_sync_action("immediate", channel, source, userid, original_message_id, original_chat_id)
            elif text == "sync_incremental":
                self._handle_sync_action("incremental", channel, source, userid, original_message_id, original_chat_id)
            elif text == "sync_full":
                self._handle_sync_action("full", channel, source, userid, original_message_id, original_chat_id)
            elif text == "stats":
                self._send_stats_info(channel, source, userid, original_message_id, original_chat_id)
            elif text == "test_notification":
                self._handle_test_notification_interactive(channel, source, userid, original_message_id, original_chat_id)
            elif text == "back":
                self._send_main_menu(channel, source, userid, original_message_id, original_chat_id)

        except Exception as e:
            logger.error(f"处理消息动作失败: {str(e)}")

    def _send_main_menu(self, channel, source, userid, original_message_id=None, original_chat_id=None):
        """发送主菜单"""
        try:
            buttons = [
                [
                    {"text": "📊 查看状态", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|status"},
                    {"text": "🔄 同步管理", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|sync_menu"}
                ],
                [
                    {"text": "📈 统计信息", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|stats"},
                    {"text": "🔔 测试通知", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|test_notification"}
                ]
            ]

            self.plugin.post_message(
                channel=channel,
                title="🔧 TransferSync 控制面板",
                text="请选择要执行的操作：",
                userid=userid,
                buttons=buttons,
                original_message_id=original_message_id,
                original_chat_id=original_chat_id
            )
        except Exception as e:
            logger.error(f"发送主菜单失败: {str(e)}")

    def _send_status_info(self, channel, source, userid, original_message_id, original_chat_id):
        """发送状态信息"""
        try:
            result = self._handle_get_status()
            status_text = self.format_command_response(result)

            buttons = [
                [{"text": "🔙 返回主菜单", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|main_menu"}]
            ]

            self.plugin.post_message(
                channel=channel,
                title="📊 TransferSync 状态",
                text=status_text,
                userid=userid,
                buttons=buttons,
                original_message_id=original_message_id,
                original_chat_id=original_chat_id
            )
        except Exception as e:
            logger.error(f"发送状态信息失败: {str(e)}")

    def _send_sync_menu(self, channel, source, userid, original_message_id, original_chat_id):
        """发送同步菜单"""
        try:
            buttons = [
                [
                    {"text": "🚀 立即同步", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|sync_immediate"},
                    {"text": "📈 增量同步", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|sync_incremental"}
                ],
                [
                    {"text": "🔄 全量同步", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|sync_full"},
                    {"text": "🔙 返回主菜单", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|main_menu"}
                ]
            ]

            self.plugin.post_message(
                channel=channel,
                title="🔄 同步管理",
                text="选择同步类型：",
                userid=userid,
                buttons=buttons,
                original_message_id=original_message_id,
                original_chat_id=original_chat_id
            )
        except Exception as e:
            logger.error(f"发送同步菜单失败: {str(e)}")

    def _handle_sync_action(self, sync_type, channel, source, userid, original_message_id, original_chat_id):
        """处理同步动作"""
        try:
            result = self._handle_trigger_sync(sync_type=sync_type)
            response_text = self.format_command_response(result)

            buttons = [
                [
                    {"text": "🔄 同步管理", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|sync_menu"},
                    {"text": "🔙 返回主菜单", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|main_menu"}
                ]
            ]

            self.plugin.post_message(
                channel=channel,
                title="✅ 同步操作完成",
                text=response_text,
                userid=userid,
                buttons=buttons,
                original_message_id=original_message_id,
                original_chat_id=original_chat_id
            )
        except Exception as e:
            logger.error(f"处理同步动作失败: {str(e)}")

    def _send_stats_info(self, channel, source, userid, original_message_id, original_chat_id):
        """发送统计信息"""
        try:
            result = self._handle_get_stats()
            stats_text = self.format_command_response(result)

            buttons = [
                [{"text": "🔙 返回主菜单", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|main_menu"}]
            ]

            self.plugin.post_message(
                channel=channel,
                title="📈 TransferSync 统计",
                text=stats_text,
                userid=userid,
                buttons=buttons,
                original_message_id=original_message_id,
                original_chat_id=original_chat_id
            )
        except Exception as e:
            logger.error(f"发送统计信息失败: {str(e)}")

    def _handle_test_notification_interactive(self, channel, source, userid, original_message_id, original_chat_id):
        """处理测试通知（交互式）"""
        try:
            result = self._handle_test_notification()
            response_text = self.format_command_response(result)

            buttons = [
                [{"text": "🔙 返回主菜单", "callback_data": f"[PLUGIN]{self.plugin.__class__.__name__}|main_menu"}]
            ]

            self.plugin.post_message(
                channel=channel,
                title="🔔 通知测试结果",
                text=response_text,
                userid=userid,
                buttons=buttons,
                original_message_id=original_message_id,
                original_chat_id=original_chat_id
            )
        except Exception as e:
            logger.error(f"处理测试通知失败: {str(e)}")