"""
类型定义和枚举类
"""
from enum import Enum
from typing import Dict


class SyncStrategy(Enum):
    """同步策略"""
    COPY = "copy"           # 复制
    MOVE = "move"           # 移动
    HARDLINK = "hardlink"   # 硬链接
    SOFTLINK = "softlink"   # 软链接


class SyncMode(Enum):
    """同步模式"""
    IMMEDIATE = "immediate"     # 立即同步
    BATCH = "batch"            # 批量同步
    QUEUE = "queue"            # 队列同步


class FileFilterType(Enum):
    """文件过滤类型"""
    EXTENSION = "extension"     # 扩展名过滤
    SIZE = "size"              # 文件大小过滤
    REGEX = "regex"            # 正则表达式过滤
    PATH = "path"              # 路径过滤


class SyncStatus(Enum):
    """同步状态"""
    IDLE = "idle"              # 空闲
    RUNNING = "running"        # 运行中
    PAUSED = "paused"          # 暂停
    ERROR = "error"            # 错误
    COMPLETED = "completed"    # 完成


class TriggerEvent(Enum):
    """可选择的触发事件"""
    TRANSFER_COMPLETE = "transfer.complete"        # 整理完成（默认）
    DOWNLOAD_ADDED = "download.added"             # 下载已添加
    SUBSCRIBE_COMPLETE = "subscribe.complete"     # 订阅已完成
    METADATA_SCRAPE = "metadata.scrape"          # 刮削元数据
    WEBHOOK_MESSAGE = "webhook.message"          # Webhook消息
    USER_MESSAGE = "user.message"                # 用户消息
    PLUGIN_TRIGGERED = "plugin.triggered"        # 插件触发事件
    MEDIA_ADDED = "media.added"                  # 媒体已添加
    FILE_MOVED = "file.moved"                    # 文件已移动
    DIRECTORY_SCAN_COMPLETE = "directory.scan.complete"  # 目录扫描完成
    SCRAPE_COMPLETE = "scrape.complete"          # 刮削完成
    
    @classmethod
    def get_display_names(cls) -> Dict[str, str]:
        """获取事件显示名称"""
        return {
            cls.TRANSFER_COMPLETE.value: "整理完成",
            cls.DOWNLOAD_ADDED.value: "下载添加", 
            cls.SUBSCRIBE_COMPLETE.value: "订阅完成",
            cls.METADATA_SCRAPE.value: "元数据刮削",
            cls.WEBHOOK_MESSAGE.value: "Webhook消息",
            cls.USER_MESSAGE.value: "用户消息",
            cls.PLUGIN_TRIGGERED.value: "插件触发",
            cls.MEDIA_ADDED.value: "媒体添加",
            cls.FILE_MOVED.value: "文件移动",
            cls.DIRECTORY_SCAN_COMPLETE.value: "目录扫描完成",
            cls.SCRAPE_COMPLETE.value: "刮削完成"
        }


class EventCondition(Enum):
    """事件过滤条件类型"""
    MEDIA_TYPE = "media_type"          # 媒体类型（电影/电视剧）
    SOURCE_PATH = "source_path"        # 源路径匹配
    TARGET_PATH = "target_path"        # 目标路径匹配
    FILE_SIZE = "file_size"           # 文件大小
    FILE_EXTENSION = "file_extension"  # 文件扩展名