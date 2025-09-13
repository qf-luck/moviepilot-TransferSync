"""
工作流动作管理模块
"""
from typing import Dict, Any, List
from app.log import logger


class WorkflowActions:
    """工作流动作管理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
    
    def get_actions(self) -> List[Dict[str, Any]]:
        """获取工作流动作列表"""
        return [
            {
                "id": "transfersync_sync_path",
                "name": "同步指定路径",
                "description": "同步指定的文件或目录",
                "version": "1.0",
                "author": "TransferSync",
                "inputs": [
                    {
                        "name": "source_path",
                        "label": "源路径",
                        "type": "text",
                        "required": True,
                        "description": "要同步的源文件或目录路径"
                    },
                    {
                        "name": "sync_strategy", 
                        "label": "同步策略",
                        "type": "select",
                        "options": [
                            {"value": "copy", "label": "复制"},
                            {"value": "move", "label": "移动"},
                            {"value": "hardlink", "label": "硬链接"},
                            {"value": "softlink", "label": "软链接"}
                        ],
                        "default": "copy",
                        "required": True
                    },
                    {
                        "name": "incremental",
                        "label": "增量同步",
                        "type": "boolean",
                        "default": True,
                        "description": "是否进行增量同步"
                    }
                ],
                "outputs": [
                    {
                        "name": "success",
                        "type": "boolean",
                        "description": "同步是否成功"
                    },
                    {
                        "name": "message",
                        "type": "text", 
                        "description": "同步结果消息"
                    },
                    {
                        "name": "files_count",
                        "type": "number",
                        "description": "同步的文件数量"
                    }
                ],
                "handler": self._handle_sync_path
            },
            {
                "id": "transfersync_conditional_sync",
                "name": "条件同步",
                "description": "根据条件决定是否同步",
                "version": "1.0", 
                "author": "TransferSync",
                "inputs": [
                    {
                        "name": "source_path",
                        "label": "源路径",
                        "type": "text",
                        "required": True
                    },
                    {
                        "name": "condition_type",
                        "label": "条件类型",
                        "type": "select",
                        "options": [
                            {"value": "file_size", "label": "文件大小"},
                            {"value": "file_type", "label": "文件类型"},
                            {"value": "file_age", "label": "文件创建时间"},
                            {"value": "directory_size", "label": "目录大小"}
                        ],
                        "required": True
                    },
                    {
                        "name": "condition_value",
                        "label": "条件值",
                        "type": "text",
                        "required": True,
                        "description": "条件判断的值"
                    },
                    {
                        "name": "operator",
                        "label": "操作符",
                        "type": "select",
                        "options": [
                            {"value": "gt", "label": "大于"},
                            {"value": "lt", "label": "小于"},
                            {"value": "eq", "label": "等于"},
                            {"value": "contains", "label": "包含"}
                        ],
                        "default": "gt"
                    }
                ],
                "outputs": [
                    {
                        "name": "condition_met",
                        "type": "boolean",
                        "description": "条件是否满足"
                    },
                    {
                        "name": "sync_result",
                        "type": "object",
                        "description": "同步结果（如果执行了同步）"
                    }
                ],
                "handler": self._handle_conditional_sync
            },
            {
                "id": "transfersync_notification",
                "name": "发送同步通知",
                "description": "发送自定义同步通知",
                "version": "1.0",
                "author": "TransferSync", 
                "inputs": [
                    {
                        "name": "title",
                        "label": "通知标题",
                        "type": "text",
                        "required": True
                    },
                    {
                        "name": "message",
                        "label": "通知内容",
                        "type": "textarea",
                        "required": True
                    },
                    {
                        "name": "channels",
                        "label": "通知渠道",
                        "type": "multiselect",
                        "options": [],  # 动态填充
                        "description": "选择通知渠道，留空则使用默认配置"
                    }
                ],
                "outputs": [
                    {
                        "name": "sent",
                        "type": "boolean",
                        "description": "通知是否发送成功"
                    }
                ],
                "handler": self._handle_send_notification
            },
            {
                "id": "transfersync_validate_path",
                "name": "验证路径",
                "description": "验证路径是否有效且可访问",
                "version": "1.0",
                "author": "TransferSync",
                "inputs": [
                    {
                        "name": "path",
                        "label": "路径",
                        "type": "text",
                        "required": True
                    },
                    {
                        "name": "check_writable",
                        "label": "检查写权限",
                        "type": "boolean",
                        "default": False
                    }
                ],
                "outputs": [
                    {
                        "name": "valid",
                        "type": "boolean",
                        "description": "路径是否有效"
                    },
                    {
                        "name": "exists",
                        "type": "boolean", 
                        "description": "路径是否存在"
                    },
                    {
                        "name": "writable",
                        "type": "boolean",
                        "description": "路径是否可写"
                    },
                    {
                        "name": "path_info",
                        "type": "object",
                        "description": "路径详细信息"
                    }
                ],
                "handler": self._handle_validate_path
            }
        ]
    
    def _handle_sync_path(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """处理同步路径动作"""
        try:
            source_path = inputs.get("source_path")
            sync_strategy = inputs.get("sync_strategy", "copy")
            incremental = inputs.get("incremental", True)
            
            if not source_path:
                return {
                    "success": False,
                    "message": "源路径不能为空",
                    "files_count": 0
                }
            
            # 临时设置同步策略
            original_strategy = self.plugin._sync_strategy
            try:
                from .sync_types import SyncStrategy
                self.plugin._sync_strategy = SyncStrategy(sync_strategy)
                
                # 执行同步
                result = self.plugin._sync_ops.sync_directory(source_path, incremental=incremental)
                
                return {
                    "success": result.get("success", False),
                    "message": result.get("message", "同步完成"),
                    "files_count": result.get("success_count", 0)
                }
            finally:
                # 恢复原始策略
                self.plugin._sync_strategy = original_strategy
                
        except Exception as e:
            logger.error(f"同步路径动作执行失败: {str(e)}")
            return {
                "success": False,
                "message": str(e),
                "files_count": 0
            }
    
    def _handle_conditional_sync(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """处理条件同步动作"""
        try:
            source_path = inputs.get("source_path")
            condition_type = inputs.get("condition_type")
            condition_value = inputs.get("condition_value")
            operator = inputs.get("operator", "gt")
            
            # 检查条件
            condition_met = self._evaluate_condition(
                source_path, condition_type, condition_value, operator
            )
            
            result = {
                "condition_met": condition_met,
                "sync_result": None
            }
            
            # 如果条件满足，执行同步
            if condition_met:
                sync_result = self.plugin._sync_ops.sync_directory(source_path)
                result["sync_result"] = sync_result
            
            return result
            
        except Exception as e:
            logger.error(f"条件同步动作执行失败: {str(e)}")
            return {
                "condition_met": False,
                "sync_result": {"success": False, "message": str(e)}
            }
    
    def _handle_send_notification(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """处理发送通知动作"""
        try:
            title = inputs.get("title", "TransferSync 通知")
            message = inputs.get("message", "")
            channels = inputs.get("channels", [])
            
            if hasattr(self.plugin, 'notification_manager'):
                # 临时设置通知渠道
                original_channels = self.plugin._notification_channels
                if channels:
                    self.plugin._notification_channels = channels
                
                try:
                    self.plugin.notification_manager.send_notification(title, message)
                    sent = True
                finally:
                    self.plugin._notification_channels = original_channels
            else:
                # 兼容旧版本
                self.plugin._send_notification(title, message)
                sent = True
            
            return {"sent": sent}
            
        except Exception as e:
            logger.error(f"发送通知动作执行失败: {str(e)}")
            return {"sent": False}
    
    def _handle_validate_path(self, inputs: Dict[str, Any]) -> Dict[str, Any]:
        """处理验证路径动作"""
        try:
            from pathlib import Path
            import os
            
            path_str = inputs.get("path", "")
            check_writable = inputs.get("check_writable", False)
            
            path = Path(path_str)
            
            # 基础验证
            exists = path.exists()
            valid = path_str and len(path_str.strip()) > 0
            
            # 权限检查
            writable = False
            if exists and check_writable:
                writable = os.access(path, os.W_OK)
            
            # 路径信息
            path_info = {
                "absolute_path": str(path.absolute()),
                "is_file": path.is_file() if exists else False,
                "is_directory": path.is_dir() if exists else False,
                "parent_exists": path.parent.exists()
            }
            
            if exists:
                stat = path.stat()
                path_info.update({
                    "size": stat.st_size,
                    "modified_time": stat.st_mtime,
                    "created_time": stat.st_ctime
                })
            
            return {
                "valid": valid,
                "exists": exists,
                "writable": writable,
                "path_info": path_info
            }
            
        except Exception as e:
            logger.error(f"验证路径动作执行失败: {str(e)}")
            return {
                "valid": False,
                "exists": False,
                "writable": False,
                "path_info": {"error": str(e)}
            }
    
    def _evaluate_condition(self, path: str, condition_type: str, condition_value: str, operator: str) -> bool:
        """评估条件"""
        try:
            from pathlib import Path
            import os
            
            path_obj = Path(path)
            if not path_obj.exists():
                return False
            
            if condition_type == "file_size":
                if path_obj.is_file():
                    actual_value = path_obj.stat().st_size
                    compare_value = int(condition_value)
                else:
                    return False
            elif condition_type == "file_type":
                if path_obj.is_file():
                    actual_value = path_obj.suffix.lower()
                    compare_value = condition_value.lower()
                else:
                    return False
            elif condition_type == "file_age":
                actual_value = path_obj.stat().st_mtime
                compare_value = float(condition_value)
            elif condition_type == "directory_size":
                if path_obj.is_dir():
                    actual_value = sum(
                        f.stat().st_size 
                        for f in path_obj.rglob('*') 
                        if f.is_file()
                    )
                    compare_value = int(condition_value)
                else:
                    return False
            else:
                return False
            
            # 执行比较
            if operator == "gt":
                return actual_value > compare_value
            elif operator == "lt":
                return actual_value < compare_value
            elif operator == "eq":
                return actual_value == compare_value
            elif operator == "contains":
                return condition_value.lower() in str(actual_value).lower()
            else:
                return False
                
        except Exception as e:
            logger.error(f"条件评估失败: {str(e)}")
            return False