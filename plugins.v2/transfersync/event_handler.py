"""
事件处理装饰器和相关功能
"""
import time
from app.core.event import Event
from app.log import logger
from .types import TriggerEvent


def event_handler(event_type: TriggerEvent):
    """统一事件处理装饰器"""
    def decorator(func):
        def wrapper(self, event: Event):
            start_time = time.time()
            try:
                # 预处理检查
                if not self._should_handle_event(event, event_type):
                    return False
                    
                logger.info(f"收到{self._get_event_display_name(event_type.value)}事件: {event}")
                
                # 执行具体的事件处理
                result = func(self, event)
                
                # 记录成功统计
                processing_time = time.time() - start_time
                self._update_event_stats(event_type, True, processing_time)
                logger.debug(f"{event_type.value}事件处理成功，耗时: {processing_time:.2f}s")
                
                return result
                
            except PermissionError as e:
                logger.error(f"处理{event_type.value}事件时权限错误: {str(e)}")
                self._update_event_stats(event_type, False, time.time() - start_time, error_type="PermissionError")
                return False
            except OSError as e:
                logger.error(f"处理{event_type.value}事件时文件系统错误: {str(e)}")
                self._update_event_stats(event_type, False, time.time() - start_time, error_type="OSError")
                return False
            except Exception as e:
                logger.error(f"处理{event_type.value}事件时未知错误: {str(e)}")
                self._update_event_stats(event_type, False, time.time() - start_time, error_type="UnknownError")
                return False
        return wrapper
    return decorator