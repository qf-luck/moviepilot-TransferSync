"""
通知管理模块
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
from app.log import logger
from app.helper.notification import NotificationHelper


class NotificationManager:
    """通知管理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self._notification_helper = NotificationHelper()
        self._available_channels = self._get_available_channels()
    
    def _get_available_channels(self) -> Dict[str, Any]:
        """获取可用的通知渠道"""
        try:
            channels = {}
            notification_services = self._notification_helper.get_services()
            for service in notification_services:
                if service.instance and hasattr(service.instance, 'send_message'):
                    channels[service.name] = {
                        "name": service.name,
                        "type": getattr(service, 'type', 'unknown'),
                        "enabled": getattr(service.instance, 'is_enabled', lambda: False)()
                    }
            return channels
        except Exception as e:
            logger.error(f"获取通知渠道失败: {str(e)}")
            return {}
    
    def send_notification(self, title: str, text: str, image: str = None):
        """发送通知 - 使用V2的NotificationHelper方式"""
        if not self.plugin._enable_notifications:
            return
        
        try:
            target_channels = []
            if self.plugin._notification_channels:
                specified_channels = (
                    self.plugin._notification_channels 
                    if isinstance(self.plugin._notification_channels, list) 
                    else [self.plugin._notification_channels]
                )
                
                for channel_name in specified_channels:
                    if channel_name in self._available_channels:
                        service_info = self._notification_helper.get_service(name=channel_name)
                        if service_info and service_info.instance:
                            target_channels.append(service_info)
            else:
                # 如果没有指定渠道，使用所有可用渠道
                all_services = self._notification_helper.get_services()
                target_channels = [s for s in all_services if s.instance and hasattr(s.instance, 'send_message')]
            
            # 发送到目标渠道
            for service_info in target_channels:
                try:
                    service_info.instance.send_message(
                        title=title,
                        text=text,
                        image=image
                    )
                    logger.info(f"通知已发送到 {service_info.name}: {title}")
                except Exception as e:
                    logger.error(f"发送通知到 {service_info.name} 失败: {str(e)}")
                    
        except Exception as e:
            logger.error(f"发送通知失败: {str(e)}")
    
    def get_available_channels(self) -> Dict[str, Any]:
        """获取可用通知渠道列表"""
        return self._available_channels
    
    def test_notification(self, channel_name: str = None) -> bool:
        """测试通知发送"""
        try:
            test_title = "TransferSync 测试通知"
            test_message = f"这是一条来自 TransferSync 插件的测试通知，发送时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            
            if channel_name:
                # 测试指定渠道
                service_info = self._notification_helper.get_service(name=channel_name)
                if service_info and service_info.instance:
                    service_info.instance.send_message(
                        title=test_title,
                        text=test_message
                    )
                    logger.info(f"测试通知已发送到 {channel_name}")
                    return True
                else:
                    logger.error(f"通知渠道 {channel_name} 不可用")
                    return False
            else:
                # 测试所有配置的渠道
                self.send_notification(test_title, test_message)
                return True
                
        except Exception as e:
            logger.error(f"测试通知发送失败: {str(e)}")
            return False
    
    def format_sync_notification(self, sync_type: str, result: Dict[str, Any]) -> tuple:
        """格式化同步通知内容"""
        try:
            if result.get('success', False):
                title = f"TransferSync {sync_type}同步完成"
                message = f"""
同步结果:
• 成功文件: {result.get('success_count', 0)}
• 失败文件: {result.get('failed_count', 0)}
• 处理时间: {result.get('processing_time', 0):.2f}秒
• 同步路径: {result.get('sync_path', '未知')}
                """.strip()
            else:
                title = f"TransferSync {sync_type}同步失败"
                message = f"同步失败: {result.get('error_message', '未知错误')}"
                
            return title, message
        except Exception as e:
            logger.error(f"格式化通知内容失败: {str(e)}")
            return "TransferSync 通知", f"格式化失败: {str(e)}"
    
    def send_sync_notification(self, sync_type: str, result: Dict[str, Any]):
        """发送同步结果通知"""
        try:
            title, message = self.format_sync_notification(sync_type, result)
            self.send_notification(title, message)
        except Exception as e:
            logger.error(f"发送同步通知失败: {str(e)}")
    
    def send_error_notification(self, error_title: str, error_message: str):
        """发送错误通知"""
        try:
            title = f"TransferSync 错误: {error_title}"
            self.send_notification(title, error_message)
        except Exception as e:
            logger.error(f"发送错误通知失败: {str(e)}")