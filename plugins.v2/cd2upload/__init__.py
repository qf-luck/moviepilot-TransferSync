import os
import shutil
import threading
import time
import json
import hashlib
import uuid
import logging.handlers
import hmac
import base64
import traceback
from datetime import datetime, timedelta
from typing import List, Tuple, Dict, Any, Optional, Union, Callable
from queue import Queue, PriorityQueue
from dataclasses import dataclass, asdict
from enum import Enum
import random
from pathlib import Path
from urllib.parse import urlencode
import requests

import pytz
from apscheduler.schedulers.background import BackgroundScheduler

try:
    from clouddrive import CloudDriveClient, Client
    from clouddrive.proto import CloudDrive_pb2
    CLOUDDRIVE_AVAILABLE = True
except ImportError:
    CloudDriveClient = None
    Client = None
    CloudDrive_pb2 = None
    CLOUDDRIVE_AVAILABLE = False

from app.core.config import settings
from app.core.context import MediaInfo
from app.core.event import eventmanager, Event
from app.core.meta import MetaBase
from app.core.metainfo import MetaInfo
from app.db import get_db
from app.db.models.transferhistory import TransferHistory
from app.db.subscribe_oper import SubscribeOper
from app.log import logger
from app.plugins import _PluginBase
from app.schemas import TransferInfo, Notification, WebhookEventInfo
from app.schemas.types import EventType, MediaType, NotificationType

lock = threading.Lock()


class UploadPriority(Enum):
    """上传任务优先级"""
    HIGH = 1      # 高优先级（收藏剧集、新剧）
    NORMAL = 2    # 普通优先级
    LOW = 3       # 低优先级（补全历史文件）


class ErrorType(Enum):
    """错误类型分类"""
    NETWORK_ERROR = "network"          # 网络错误，可重试
    PERMISSION_ERROR = "permission"    # 权限错误，不可重试
    DISK_FULL = "disk_full"           # 磁盘空间不足，可延迟重试
    FILE_NOT_FOUND = "file_not_found" # 文件不存在，不可重试
    TEMPORARY_ERROR = "temporary"      # 临时错误，可重试
    UNKNOWN_ERROR = "unknown"          # 未知错误，可重试


@dataclass
class UploadTask:
    """上传任务数据类"""
    file_path: str
    cd2_dest: str
    priority: UploadPriority = UploadPriority.NORMAL
    created_time: float = None
    retry_count: int = 0
    media_info: Any = None
    meta: Any = None
    last_error: str = None
    error_type: ErrorType = None
    next_retry_time: float = None

    def __post_init__(self):
        if self.created_time is None:
            self.created_time = time.time()

    def __lt__(self, other):
        # 优先级越小越优先，如果优先级相同按创建时间排序
        if self.priority.value == other.priority.value:
            return self.created_time < other.created_time
        return self.priority.value < other.priority.value

    def is_ready_for_retry(self) -> bool:
        """检查是否准备好重试"""
        if self.next_retry_time is None:
            return True
        return time.time() >= self.next_retry_time

    def calculate_next_retry_time(self, base_delay: int = 2, max_delay: int = 300, enable_jitter: bool = True) -> float:
        """计算下次重试时间"""
        # 指数退避算法
        delay = min(base_delay * (2 ** self.retry_count), max_delay)
        
        # 添加抖动避免雷群效应
        if enable_jitter:
            jitter = random.uniform(0, delay * 0.1)
            delay += jitter
            
        self.next_retry_time = time.time() + delay
        return self.next_retry_time


class UploadQueue:
    """上传队列管理器"""
    
    def __init__(self, max_concurrent_uploads=3):
        self.queue = PriorityQueue()
        self.active_uploads = {}  # 正在上传的任务
        self.completed_uploads = []  # 已完成的任务
        self.failed_uploads = []  # 失败的任务
        self.max_concurrent = max_concurrent_uploads
        self.lock = threading.Lock()
        self.stats = {
            'total_queued': 0,
            'total_processed': 0,
            'total_success': 0,
            'total_failed': 0
        }

    def add_task(self, task: UploadTask):
        """添加上传任务到队列"""
        with self.lock:
            self.queue.put(task)
            self.stats['total_queued'] += 1

    def get_next_task(self) -> Optional[UploadTask]:
        """获取下一个待执行的任务"""
        try:
            if len(self.active_uploads) < self.max_concurrent:
                # 寻找准备好重试的任务
                temp_tasks = []
                while not self.queue.empty():
                    task = self.queue.get_nowait()
                    if task.is_ready_for_retry():
                        self.active_uploads[task.file_path] = task
                        # 将暂存的任务重新放回队列
                        for temp_task in temp_tasks:
                            self.queue.put(temp_task)
                        return task
                    else:
                        temp_tasks.append(task)
                
                # 如果没有准备好的任务，将所有任务重新放回队列
                for temp_task in temp_tasks:
                    self.queue.put(temp_task)
        except:
            pass
        return None

    def mark_task_completed(self, task: UploadTask, success: bool):
        """标记任务完成"""
        with self.lock:
            if task.file_path in self.active_uploads:
                del self.active_uploads[task.file_path]
            
            self.stats['total_processed'] += 1
            
            if success:
                self.completed_uploads.append(task)
                self.stats['total_success'] += 1
            else:
                self.failed_uploads.append(task)
                self.stats['total_failed'] += 1

    def retry_task(self, task: UploadTask, max_attempts: int = 5, base_delay: int = 2, max_delay: int = 300, enable_jitter: bool = True):
        """智能重试任务"""
        if task.retry_count < max_attempts:
            task.retry_count += 1
            
            # 计算下次重试时间
            task.calculate_next_retry_time(base_delay, max_delay, enable_jitter)
            
            # 重试任务提高优先级（但不超过HIGH）
            if task.priority == UploadPriority.LOW:
                task.priority = UploadPriority.NORMAL
            elif task.priority == UploadPriority.NORMAL:
                task.priority = UploadPriority.HIGH
                
            self.add_task(task)
            
            with self.lock:
                if task in self.failed_uploads:
                    self.failed_uploads.remove(task)
                    self.stats['total_failed'] -= 1

    def get_queue_status(self) -> Dict:
        """获取队列状态"""
        with self.lock:
            return {
                'queued': self.queue.qsize(),
                'active': len(self.active_uploads),
                'completed': len(self.completed_uploads),
                'failed': len(self.failed_uploads),
                'stats': self.stats.copy()
            }

    def clear_completed_history(self):
        """清理已完成的历史记录"""
        with self.lock:
            # 只保留最近100条完成记录
            if len(self.completed_uploads) > 100:
                self.completed_uploads = self.completed_uploads[-100:]
            # 只保留最近50条失败记录
            if len(self.failed_uploads) > 50:
                self.failed_uploads = self.failed_uploads[-50:]


class UploadStatistics:
    """上传统计管理器"""
    
    def __init__(self):
        self.daily_stats = {}  # 按日期统计
        self.hourly_stats = {}  # 按小时统计
        self.file_type_stats = {}  # 按文件类型统计
        self.error_stats = {}  # 错误统计
        self.performance_stats = {
            'avg_upload_time': 0,
            'total_uploaded_size': 0,
            'peak_concurrent_uploads': 0,
            'uptime_start': time.time()
        }
        self.lock = threading.Lock()

    def record_upload_attempt(self, file_path: str, file_size: int = 0):
        """记录上传尝试"""
        today = datetime.now().strftime('%Y-%m-%d')
        hour = datetime.now().strftime('%Y-%m-%d %H:00')
        file_ext = os.path.splitext(file_path)[1].lower()
        
        with self.lock:
            # 日统计
            if today not in self.daily_stats:
                self.daily_stats[today] = {'attempts': 0, 'success': 0, 'failed': 0, 'size': 0}
            self.daily_stats[today]['attempts'] += 1
            
            # 小时统计
            if hour not in self.hourly_stats:
                self.hourly_stats[hour] = {'attempts': 0, 'success': 0, 'failed': 0, 'size': 0}
            self.hourly_stats[hour]['attempts'] += 1
            
            # 文件类型统计
            if file_ext not in self.file_type_stats:
                self.file_type_stats[file_ext] = {'attempts': 0, 'success': 0, 'failed': 0, 'size': 0}
            self.file_type_stats[file_ext]['attempts'] += 1
            self.file_type_stats[file_ext]['size'] += file_size

    def record_upload_result(self, file_path: str, success: bool, duration: float = 0, file_size: int = 0, error_type: str = None):
        """记录上传结果"""
        today = datetime.now().strftime('%Y-%m-%d')
        hour = datetime.now().strftime('%Y-%m-%d %H:00')
        file_ext = os.path.splitext(file_path)[1].lower()
        
        with self.lock:
            # 更新各项统计
            if success:
                self.daily_stats[today]['success'] += 1
                self.hourly_stats[hour]['success'] += 1
                self.file_type_stats[file_ext]['success'] += 1
                self.performance_stats['total_uploaded_size'] += file_size
                
                # 更新平均上传时间
                if duration > 0:
                    current_avg = self.performance_stats['avg_upload_time']
                    total_success = sum(stats['success'] for stats in self.daily_stats.values())
                    self.performance_stats['avg_upload_time'] = (current_avg * (total_success - 1) + duration) / total_success
            else:
                self.daily_stats[today]['failed'] += 1
                self.hourly_stats[hour]['failed'] += 1
                self.file_type_stats[file_ext]['failed'] += 1
                
                # 错误统计
                if error_type:
                    if error_type not in self.error_stats:
                        self.error_stats[error_type] = 0
                    self.error_stats[error_type] += 1

    def update_concurrent_peak(self, current_concurrent: int):
        """更新并发峰值"""
        with self.lock:
            if current_concurrent > self.performance_stats['peak_concurrent_uploads']:
                self.performance_stats['peak_concurrent_uploads'] = current_concurrent

    def get_daily_summary(self, days: int = 7) -> Dict:
        """获取每日统计摘要"""
        with self.lock:
            recent_days = {}
            base_date = datetime.now() - timedelta(days=days-1)
            
            for i in range(days):
                date_key = (base_date + timedelta(days=i)).strftime('%Y-%m-%d')
                recent_days[date_key] = self.daily_stats.get(date_key, {
                    'attempts': 0, 'success': 0, 'failed': 0, 'size': 0
                })
                
            return recent_days

    def get_performance_summary(self) -> Dict:
        """获取性能统计摘要"""
        with self.lock:
            uptime = time.time() - self.performance_stats['uptime_start']
            total_success = sum(stats['success'] for stats in self.daily_stats.values())
            total_failed = sum(stats['failed'] for stats in self.daily_stats.values())
            
            return {
                'uptime_hours': round(uptime / 3600, 2),
                'total_uploads': total_success,
                'total_failures': total_failed,
                'success_rate': round(total_success / (total_success + total_failed) * 100, 2) if (total_success + total_failed) > 0 else 0,
                'avg_upload_time': round(self.performance_stats['avg_upload_time'], 2),
                'total_uploaded_size_gb': round(self.performance_stats['total_uploaded_size'] / (1024**3), 2),
                'peak_concurrent_uploads': self.performance_stats['peak_concurrent_uploads'],
                'uploads_per_hour': round(total_success / (uptime / 3600), 2) if uptime > 0 else 0
            }

    def get_error_analysis(self) -> Dict:
        """获取错误分析"""
        with self.lock:
            return dict(sorted(self.error_stats.items(), key=lambda x: x[1], reverse=True))

    def cleanup_old_data(self, keep_days: int = 30):
        """清理旧数据"""
        cutoff_date = datetime.now() - timedelta(days=keep_days)
        cutoff_str = cutoff_date.strftime('%Y-%m-%d')
        
        with self.lock:
            # 清理日统计
            self.daily_stats = {k: v for k, v in self.daily_stats.items() if k >= cutoff_str}
            
            # 清理小时统计
            cutoff_hour = cutoff_date.strftime('%Y-%m-%d %H:00')
            self.hourly_stats = {k: v for k, v in self.hourly_stats.items() if k >= cutoff_hour}


class EnterpriseLogger:
    """企业级日志管理器"""
    
    def __init__(self, plugin_name: str, log_dir: str = None):
        self.plugin_name = plugin_name
        self.log_dir = Path(log_dir) if log_dir else Path("logs")
        self.log_dir.mkdir(exist_ok=True)
        
        # 创建专用的企业级logger
        self.logger = logging.getLogger(f"enterprise_{plugin_name}")
        self.logger.setLevel(logging.INFO)
        self.logger.handlers.clear()
        
        # 业务操作日志
        self.business_logger = self._create_rotating_logger(
            "business", 
            self.log_dir / f"{plugin_name}_business.log"
        )
        
        # 性能监控日志
        self.performance_logger = self._create_rotating_logger(
            "performance", 
            self.log_dir / f"{plugin_name}_performance.log"
        )
        
        # 安全审计日志
        self.audit_logger = self._create_rotating_logger(
            "audit", 
            self.log_dir / f"{plugin_name}_audit.log"
        )
        
        # 错误日志
        self.error_logger = self._create_rotating_logger(
            "error", 
            self.log_dir / f"{plugin_name}_error.log"
        )
        
        self.session_id = str(uuid.uuid4())[:8]
    
    def _create_rotating_logger(self, name: str, filepath: Path) -> logging.Logger:
        """创建轮转日志记录器"""
        logger = logging.getLogger(f"{self.plugin_name}_{name}")
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        
        # 使用RotatingFileHandler实现日志轮转
        handler = logging.handlers.RotatingFileHandler(
            filepath, maxBytes=10*1024*1024, backupCount=5, encoding='utf-8'
        )
        
        formatter = logging.Formatter(
            '%(asctime)s | %(levelname)s | %(name)s | %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
        return logger
    
    def log_business_event(self, event_type: str, details: Dict, user_id: str = None, 
                          file_path: str = None, status: str = "INFO"):
        """记录业务事件"""
        log_entry = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "user_id": user_id or "system",
            "file_path": file_path,
            "status": status,
            "details": details
        }
        self.business_logger.info(json.dumps(log_entry, ensure_ascii=False))
    
    def log_performance_metric(self, metric_name: str, value: Union[int, float], 
                              unit: str = None, tags: Dict = None):
        """记录性能指标"""
        log_entry = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "metric_name": metric_name,
            "value": value,
            "unit": unit,
            "tags": tags or {}
        }
        self.performance_logger.info(json.dumps(log_entry, ensure_ascii=False))
    
    def log_audit_event(self, action: str, resource: str, user_id: str = None, 
                       result: str = "SUCCESS", details: Dict = None):
        """记录审计事件"""
        log_entry = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "action": action,
            "resource": resource,
            "user_id": user_id or "system",
            "result": result,
            "details": details or {}
        }
        self.audit_logger.info(json.dumps(log_entry, ensure_ascii=False))
    
    def log_error(self, error_type: str, error_message: str, stack_trace: str = None,
                  context: Dict = None):
        """记录错误"""
        log_entry = {
            "session_id": self.session_id,
            "timestamp": datetime.now().isoformat(),
            "error_type": error_type,
            "error_message": error_message,
            "stack_trace": stack_trace,
            "context": context or {}
        }
        self.error_logger.error(json.dumps(log_entry, ensure_ascii=False))


class DistributedLock:
    """分布式锁实现"""
    
    def __init__(self, resource_name: str, timeout: int = 300):
        self.resource_name = resource_name
        self.timeout = timeout
        self.lock_id = str(uuid.uuid4())
        self.acquired = False
        self.lock_file = Path(f"/tmp/cd2upload_lock_{hashlib.md5(resource_name.encode()).hexdigest()}")
        
    def acquire(self) -> bool:
        """获取锁"""
        try:
            current_time = time.time()
            
            # 检查锁文件是否存在
            if self.lock_file.exists():
                with open(self.lock_file, 'r') as f:
                    lock_data = json.loads(f.read())
                    
                # 检查锁是否过期
                if current_time - lock_data['acquired_time'] > self.timeout:
                    # 锁已过期，删除旧锁
                    self.lock_file.unlink(missing_ok=True)
                else:
                    return False
            
            # 创建新锁
            lock_data = {
                'lock_id': self.lock_id,
                'resource_name': self.resource_name,
                'acquired_time': current_time,
                'timeout': self.timeout
            }
            
            with open(self.lock_file, 'w') as f:
                f.write(json.dumps(lock_data))
            
            self.acquired = True
            return True
            
        except Exception:
            return False
    
    def release(self) -> bool:
        """释放锁"""
        try:
            if not self.acquired:
                return True
                
            if not self.lock_file.exists():
                return True
                
            with open(self.lock_file, 'r') as f:
                lock_data = json.loads(f.read())
                
            # 验证锁的所有权
            if lock_data['lock_id'] == self.lock_id:
                self.lock_file.unlink()
                self.acquired = False
                return True
                
            return False
            
        except Exception:
            return False
    
    def __enter__(self):
        if not self.acquire():
            raise RuntimeError(f"无法获取资源锁: {self.resource_name}")
        return self
        
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.release()


class QuotaManager:
    """配额管理器"""
    
    def __init__(self):
        self.quotas = {}
        self.usage = {}
        self.lock = threading.Lock()
    
    def set_quota(self, resource_type: str, limit: int, window_seconds: int = 3600):
        """设置配额限制"""
        with self.lock:
            self.quotas[resource_type] = {
                'limit': limit,
                'window': window_seconds,
                'reset_time': time.time() + window_seconds
            }
            if resource_type not in self.usage:
                self.usage[resource_type] = 0
    
    def check_quota(self, resource_type: str, requested: int = 1) -> bool:
        """检查配额是否允许请求"""
        with self.lock:
            if resource_type not in self.quotas:
                return True
                
            quota = self.quotas[resource_type]
            current_time = time.time()
            
            # 检查是否需要重置配额
            if current_time >= quota['reset_time']:
                self.usage[resource_type] = 0
                quota['reset_time'] = current_time + quota['window']
            
            # 检查配额
            if self.usage[resource_type] + requested <= quota['limit']:
                self.usage[resource_type] += requested
                return True
            
            return False
    
    def get_quota_status(self, resource_type: str) -> Dict:
        """获取配额状态"""
        with self.lock:
            if resource_type not in self.quotas:
                return {"error": "配额未设置"}
                
            quota = self.quotas[resource_type]
            current_time = time.time()
            
            return {
                "limit": quota['limit'],
                "used": self.usage.get(resource_type, 0),
                "remaining": max(0, quota['limit'] - self.usage.get(resource_type, 0)),
                "reset_in": max(0, int(quota['reset_time'] - current_time)),
                "reset_time": datetime.fromtimestamp(quota['reset_time']).isoformat()
            }


class HealthChecker:
    """健康检查器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self.health_status = {
            "overall": "healthy",
            "components": {},
            "last_check": None
        }
        self.lock = threading.Lock()
    
    def check_health(self) -> Dict:
        """执行健康检查"""
        with self.lock:
            checks = {
                "queue_health": self._check_queue_health(),
                "storage_health": self._check_storage_health(),
                "cd2_clients_health": self._check_cd2_clients_health(),
                "statistics_health": self._check_statistics_health()
            }
            
            # 计算整体健康状态
            failed_checks = [k for k, v in checks.items() if v.get("status") != "healthy"]
            overall_status = "unhealthy" if failed_checks else "healthy"
            
            self.health_status = {
                "overall": overall_status,
                "components": checks,
                "last_check": datetime.now().isoformat(),
                "failed_components": failed_checks
            }
            
            return self.health_status
    
    def _check_queue_health(self) -> Dict:
        """检查队列健康状态"""
        try:
            if not self.plugin._upload_queue:
                return {"status": "disabled", "message": "队列管理未启用"}
                
            queue_status = self.plugin._upload_queue.get_queue_status()
            
            # 检查队列是否阻塞
            if queue_status.get("active", 0) == 0 and queue_status.get("queued", 0) > 0:
                return {"status": "warning", "message": "队列可能阻塞", "details": queue_status}
            
            return {"status": "healthy", "details": queue_status}
            
        except Exception as e:
            return {"status": "unhealthy", "message": f"队列检查失败: {e}"}
    
    def _check_storage_health(self) -> Dict:
        """检查存储健康状态"""
        try:
            # 检查软链接目录
            softlink_path = Path(self.plugin._softlink_prefix_path)
            if not softlink_path.exists():
                return {"status": "warning", "message": "软链接目录不存在"}
            
            # 检查CloudDrive2挂载目录
            cd2_path = Path(self.plugin._cd_mount_prefix_path)
            if not cd2_path.parent.exists():
                return {"status": "warning", "message": "CloudDrive2挂载目录不存在"}
            
            # 检查磁盘空间
            statvfs = os.statvfs(cd2_path.parent)
            free_space = statvfs.f_bavail * statvfs.f_frsize
            total_space = statvfs.f_blocks * statvfs.f_frsize
            usage_percent = (1 - free_space / total_space) * 100
            
            if usage_percent > 95:
                return {"status": "critical", "message": "磁盘空间不足", 
                       "details": {"usage_percent": round(usage_percent, 2)}}
            elif usage_percent > 85:
                return {"status": "warning", "message": "磁盘空间紧张",
                       "details": {"usage_percent": round(usage_percent, 2)}}
            
            return {"status": "healthy", "details": {"usage_percent": round(usage_percent, 2)}}
            
        except Exception as e:
            return {"status": "unhealthy", "message": f"存储检查失败: {e}"}
    
    def _check_cd2_clients_health(self) -> Dict:
        """检查CloudDrive2客户端健康状态"""
        try:
            if not self.plugin._cd2_clients:
                return {"status": "warning", "message": "未配置CloudDrive2客户端"}
                
            healthy_clients = 0
            total_clients = len(self.plugin._cd2_clients)
            
            for name, client in self.plugin._cd2_clients.items():
                try:
                    # 简单的健康检查：尝试获取文件系统
                    fs = client.fs
                    if fs:
                        healthy_clients += 1
                except Exception:
                    pass
            
            health_ratio = healthy_clients / total_clients
            
            if health_ratio == 0:
                return {"status": "critical", "message": "所有CD2客户端不可用"}
            elif health_ratio < 0.5:
                return {"status": "warning", "message": f"部分CD2客户端不可用 ({healthy_clients}/{total_clients})"}
            
            return {"status": "healthy", "details": {"healthy_clients": healthy_clients, "total_clients": total_clients}}
            
        except Exception as e:
            return {"status": "unhealthy", "message": f"CD2客户端检查失败: {e}"}
    
    def _check_statistics_health(self) -> Dict:
        """检查统计系统健康状态"""
        try:
            if not self.plugin._statistics:
                return {"status": "disabled", "message": "统计功能未启用"}
                
            # 检查统计数据的时效性
            if not self.plugin._statistics.daily_stats:
                return {"status": "warning", "message": "没有统计数据"}
                
            latest_date = max(self.plugin._statistics.daily_stats.keys())
            today = datetime.now().strftime('%Y-%m-%d')
            
            if latest_date != today:
                return {"status": "warning", "message": "统计数据不是最新的"}
                
            return {"status": "healthy"}
            
        except Exception as e:
            return {"status": "unhealthy", "message": f"统计系统检查失败: {e}"}


class APIHandler:
    """REST API处理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self.api_routes = {}
        self._setup_routes()
    
    def _setup_routes(self):
        """设置API路由"""
        self.api_routes = {
            '/api/cd2upload/status': self._handle_status,
            '/api/cd2upload/health': self._handle_health,
            '/api/cd2upload/statistics': self._handle_statistics,
            '/api/cd2upload/queue': self._handle_queue,
            '/api/cd2upload/enterprise': self._handle_enterprise,
            '/api/cd2upload/upload': self._handle_manual_upload,
            '/api/cd2upload/config': self._handle_config,
        }
    
    def handle_request(self, path: str, method: str, params: Dict = None, headers: Dict = None) -> Dict:
        """处理API请求"""
        try:
            if path not in self.api_routes:
                return {"error": "API路径不存在", "code": 404}
                
            handler = self.api_routes[path]
            return handler(method, params or {}, headers or {})
            
        except Exception as e:
            if self.plugin._enterprise_logger:
                self.plugin._enterprise_logger.log_error(
                    "api_request_error",
                    str(e),
                    traceback.format_exc(),
                    {"path": path, "method": method}
                )
            return {"error": f"API请求处理失败: {str(e)}", "code": 500}
    
    def _handle_status(self, method: str, params: Dict, headers: Dict) -> Dict:
        """处理状态查询"""
        if method != "GET":
            return {"error": "方法不允许", "code": 405}
            
        return {
            "plugin": {
                "name": self.plugin.plugin_name,
                "version": self.plugin.plugin_version,
                "status": "running"
            },
            "queue_status": self.plugin.get_queue_status(),
            "timestamp": datetime.now().isoformat()
        }
    
    def _handle_health(self, method: str, params: Dict, headers: Dict) -> Dict:
        """处理健康检查"""
        if method != "GET":
            return {"error": "方法不允许", "code": 405}
            
        health_status = self.plugin._health_checker.check_health() if self.plugin._health_checker else {"status": "disabled"}
        return health_status
    
    def _handle_statistics(self, method: str, params: Dict, headers: Dict) -> Dict:
        """处理统计查询"""
        if method != "GET":
            return {"error": "方法不允许", "code": 405}
            
        return self.plugin.get_statistics_dashboard()
    
    def _handle_queue(self, method: str, params: Dict, headers: Dict) -> Dict:
        """处理队列管理"""
        if method == "GET":
            return self.plugin.get_queue_status()
        elif method == "POST":
            action = params.get("action")
            if action == "clear":
                # 清空队列（需要权限验证）
                if self.plugin._upload_queue:
                    self.plugin._upload_queue.clear_completed_history()
                return {"message": "队列已清理", "code": 200}
            else:
                return {"error": "不支持的操作", "code": 400}
        else:
            return {"error": "方法不允许", "code": 405}
    
    def _handle_enterprise(self, method: str, params: Dict, headers: Dict) -> Dict:
        """处理企业级功能"""
        if method != "GET":
            return {"error": "方法不允许", "code": 405}
            
        return self.plugin.get_enterprise_status()
    
    def _handle_manual_upload(self, method: str, params: Dict, headers: Dict) -> Dict:
        """处理手动上传请求"""
        if method != "POST":
            return {"error": "方法不允许", "code": 405}
            
        files = params.get("files", [])
        if not files:
            return {"error": "文件列表为空", "code": 400}
            
        # 验证配额
        if self.plugin._quota_manager and not self.plugin._quota_manager.check_quota("upload_requests", len(files)):
            return {"error": "超出上传配额限制", "code": 429}
        
        # 加入上传队列
        if self.plugin._upload_queue:
            for file_path in files:
                cd2_dest = file_path.replace(self.plugin._softlink_prefix_path, self.plugin._cd_mount_prefix_path)
                task = UploadTask(file_path=file_path, cd2_dest=cd2_dest, priority=UploadPriority.HIGH)
                self.plugin._upload_queue.add_task(task)
                
        return {"message": f"已加入 {len(files)} 个文件到上传队列", "code": 200}
    
    def _handle_config(self, method: str, params: Dict, headers: Dict) -> Dict:
        """处理配置管理"""
        if method == "GET":
            # 返回配置概要（敏感信息除外）
            return {
                "queue_management": self.plugin._enable_queue_management,
                "statistics": self.plugin._enable_statistics,
                "enterprise_logging": self.plugin._enable_enterprise_logging,
                "health_check": self.plugin._enable_health_check,
                "quota_limit": self.plugin._quota_upload_limit,
                "max_concurrent": self.plugin._max_concurrent_uploads
            }
        else:
            return {"error": "配置修改需要通过Web界面", "code": 403}


class WebHookManager:
    """WebHook管理器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self.webhooks = {}
        self.webhook_queue = Queue()
        self.webhook_thread = None
        self._running = False
        
    def start(self):
        """启动WebHook处理线程"""
        if self._running:
            return
            
        self._running = True
        self.webhook_thread = threading.Thread(target=self._process_webhooks, daemon=True)
        self.webhook_thread.start()
        
    def stop(self):
        """停止WebHook处理"""
        self._running = False
        if self.webhook_thread:
            self.webhook_thread.join(timeout=5)
    
    def register_webhook(self, event_type: str, url: str, secret: str = None, 
                        headers: Dict = None, retry_count: int = 3):
        """注册WebHook"""
        webhook_id = str(uuid.uuid4())
        self.webhooks[webhook_id] = {
            "event_type": event_type,
            "url": url,
            "secret": secret,
            "headers": headers or {},
            "retry_count": retry_count,
            "created_time": datetime.now().isoformat()
        }
        
        if self.plugin._enterprise_logger:
            self.plugin._enterprise_logger.log_audit_event(
                "webhook_registered", 
                f"webhook:{webhook_id}",
                details={"event_type": event_type, "url": url}
            )
            
        return webhook_id
    
    def unregister_webhook(self, webhook_id: str) -> bool:
        """注销WebHook"""
        if webhook_id in self.webhooks:
            del self.webhooks[webhook_id]
            
            if self.plugin._enterprise_logger:
                self.plugin._enterprise_logger.log_audit_event(
                    "webhook_unregistered",
                    f"webhook:{webhook_id}"
                )
            return True
        return False
    
    def trigger_webhook(self, event_type: str, data: Dict):
        """触发WebHook"""
        for webhook_id, webhook in self.webhooks.items():
            if webhook["event_type"] == event_type or webhook["event_type"] == "*":
                self.webhook_queue.put({
                    "webhook_id": webhook_id,
                    "webhook": webhook,
                    "data": data,
                    "timestamp": datetime.now().isoformat()
                })
    
    def _process_webhooks(self):
        """处理WebHook队列"""
        while self._running:
            try:
                webhook_event = self.webhook_queue.get(timeout=1)
                self._send_webhook(webhook_event)
            except:
                continue
    
    def _send_webhook(self, webhook_event: Dict):
        """发送WebHook"""
        webhook = webhook_event["webhook"]
        data = webhook_event["data"]
        
        payload = {
            "event_type": webhook["event_type"],
            "data": data,
            "timestamp": webhook_event["timestamp"]
        }
        
        headers = webhook["headers"].copy()
        headers["Content-Type"] = "application/json"
        
        # 添加签名（如果有密钥）
        if webhook.get("secret"):
            signature = self._generate_signature(webhook["secret"], json.dumps(payload))
            headers["X-Signature"] = signature
        
        # 重试逻辑
        for attempt in range(webhook["retry_count"]):
            try:
                response = requests.post(
                    webhook["url"],
                    json=payload,
                    headers=headers,
                    timeout=30
                )
                
                if response.status_code < 400:
                    # 成功
                    if self.plugin._enterprise_logger:
                        self.plugin._enterprise_logger.log_business_event(
                            "webhook_sent",
                            {
                                "webhook_id": webhook_event["webhook_id"],
                                "status_code": response.status_code,
                                "attempt": attempt + 1
                            }
                        )
                    break
                else:
                    raise requests.RequestException(f"HTTP {response.status_code}")
                    
            except Exception as e:
                if attempt == webhook["retry_count"] - 1:
                    # 最后一次尝试失败
                    if self.plugin._enterprise_logger:
                        self.plugin._enterprise_logger.log_error(
                            "webhook_failed",
                            str(e),
                            context={
                                "webhook_id": webhook_event["webhook_id"],
                                "url": webhook["url"],
                                "attempts": attempt + 1
                            }
                        )
                else:
                    # 等待后重试
                    time.sleep(2 ** attempt)
    
    def _generate_signature(self, secret: str, payload: str) -> str:
        """生成WebHook签名"""
        signature = hmac.new(
            secret.encode('utf-8'),
            payload.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"
    
    def list_webhooks(self) -> List[Dict]:
        """列出所有WebHook"""
        return [
            {
                "webhook_id": webhook_id,
                "event_type": webhook["event_type"],
                "url": webhook["url"],
                "created_time": webhook["created_time"]
            }
            for webhook_id, webhook in self.webhooks.items()
        ]


class Cd2Upload(_PluginBase):
    # 插件名称
    plugin_name = "CloudDrive2智能上传"
    # 插件描述
    plugin_desc = "智能上传媒体文件到CloudDrive2，支持上传监控、任务管理、通知Cloud Media Sync处理后续文件管理"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/thsrite/MoviePilot-Plugins/main/icons/clouddrive.png"
    # 插件版本
    plugin_version = "2.2.0"
    # 插件作者
    plugin_author = "honue & enhanced"
    # 作者主页
    author_url = "https://github.com/honue"
    # 插件配置项ID前缀
    plugin_config_prefix = "cd2upload_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 1

    _enable = True
    _cron = '20'
    _onlyonce = False
    _cleanlink = False
    _monitor_upload = True
    _notify_upload = False
    _upload_retry_count = 3
    _cd2_confs = None
    _cloud_media_sync = True
    _monitor_interval = 10
    _clean_interval = 20
    _enable_cookie_check = True
    _cookie_check_interval = 30
    _black_dirs = ""
    _upload_timeout = 300
    _delete_source_after_upload = False
    _enable_favorite_notify = True
    _notification_type = "Plugin"
    _notification_channels = ""
    _enable_progress_notify = False
    _enable_detailed_stats = True
    
    # 上传队列管理配置
    _enable_queue_management = True
    _max_concurrent_uploads = 3
    _queue_check_interval = 5
    
    # 增强错误处理配置
    _enable_smart_retry = True
    _max_retry_attempts = 5
    _retry_base_delay = 2
    _retry_max_delay = 300
    _enable_jitter = True
    
    # 统计和监控配置
    _enable_statistics = True
    _stats_cleanup_days = 30
    _enable_performance_monitoring = True
    
    # 企业级功能配置
    _enable_enterprise_logging = True
    _enable_distributed_lock = True
    _enable_quota_management = True
    _enable_health_check = True
    _log_retention_days = 30
    _quota_upload_limit = 1000
    _quota_window_hours = 24
    
    # API和WebHook配置
    _enable_api = True
    _enable_webhook = True
    _api_timeout = 30
    _webhook_secret = ""

    # 链接前缀（用于路径替换）
    _softlink_prefix_path = '/strm/'
    # cd2挂载媒体库前缀（用于通知Cloud Media Sync）
    _cd_mount_prefix_path = '/CloudNAS/115/emby/'

    _scheduler = None
    _cd2_clients = {}
    _clients = {}
    _cd2_url = {}
    _upload_queue = None
    _statistics = None
    _enterprise_logger = None
    _quota_manager = None
    _health_checker = None
    _api_handler = None
    _webhook_manager = None

    _subscribe_oper = SubscribeOper()

    def init_plugin(self, config: dict = None):
        # 检查版本兼容性
        try:
            if hasattr(settings, 'VERSION_FLAG'):
                version = settings.VERSION_FLAG  # V2
                logger.info("检测到MoviePilot V2版本")
            else:
                version = "v1"
                logger.info("检测到MoviePilot V1版本")
        except Exception as e:
            logger.warning(f"版本检测失败: {e}")
            version = "unknown"

        # 检查 clouddrive 依赖是否可用
        if not CLOUDDRIVE_AVAILABLE:
            logger.error("CloudDrive2智能上传启动失败：缺少 clouddrive 依赖库")
            logger.error("请安装依赖：pip install clouddrive")
            self.systemmessage.put("CloudDrive2智能上传启动失败：缺少 clouddrive 依赖库，请安装：pip install clouddrive")
            return

        if config:
            self._enable = config.get('enable', False)
            self._cron: int = int(config.get('cron', '20'))
            self._onlyonce = config.get('onlyonce', False)
            self._cleanlink = config.get('cleanlink', False)
            self._monitor_upload = config.get('monitor_upload', True)
            self._notify_upload = config.get('notify_upload', False)
            self._upload_retry_count = config.get('upload_retry_count', 3)
            self._cd2_confs = config.get('cd2_confs', '')
            self._cloud_media_sync = config.get('cloud_media_sync', True)
            self._monitor_interval = config.get('monitor_interval', 10)
            self._clean_interval = config.get('clean_interval', 20)
            self._enable_cookie_check = config.get('enable_cookie_check', True)
            self._cookie_check_interval = config.get('cookie_check_interval', 30)
            self._black_dirs = config.get('black_dirs', '')
            self._upload_timeout = config.get('upload_timeout', 300)
            self._delete_source_after_upload = config.get('delete_source_after_upload', False)
            self._enable_favorite_notify = config.get('enable_favorite_notify', True)
            self._notification_type = config.get('notification_type', 'Plugin')
            self._notification_channels = config.get('notification_channels', '')
            self._enable_progress_notify = config.get('enable_progress_notify', False)
            self._enable_detailed_stats = config.get('enable_detailed_stats', True)
            self._enable_queue_management = config.get('enable_queue_management', True)
            self._max_concurrent_uploads = config.get('max_concurrent_uploads', 3)
            self._queue_check_interval = config.get('queue_check_interval', 5)
            self._enable_smart_retry = config.get('enable_smart_retry', True)
            self._max_retry_attempts = config.get('max_retry_attempts', 5)
            self._retry_base_delay = config.get('retry_base_delay', 2)
            self._retry_max_delay = config.get('retry_max_delay', 300)
            self._enable_jitter = config.get('enable_jitter', True)
            self._enable_statistics = config.get('enable_statistics', True)
            self._stats_cleanup_days = config.get('stats_cleanup_days', 30)
            self._enable_performance_monitoring = config.get('enable_performance_monitoring', True)
            self._enable_enterprise_logging = config.get('enable_enterprise_logging', True)
            self._enable_distributed_lock = config.get('enable_distributed_lock', True)
            self._enable_quota_management = config.get('enable_quota_management', True)
            self._enable_health_check = config.get('enable_health_check', True)
            self._log_retention_days = config.get('log_retention_days', 30)
            self._quota_upload_limit = config.get('quota_upload_limit', 1000)
            self._quota_window_hours = config.get('quota_window_hours', 24)
            self._enable_api = config.get('enable_api', True)
            self._enable_webhook = config.get('enable_webhook', True)
            self._api_timeout = config.get('api_timeout', 30)
            self._webhook_secret = config.get('webhook_secret', '')
            self._softlink_prefix_path = config.get('softlink_prefix_path', '/strm/')
            self._cd_mount_prefix_path = config.get('cd_mount_prefix_path', '/CloudNAS/CloudDrive/115/emby/')

        self.stop_service()

        if not self._enable:
            return

        # 初始化CloudDrive2客户端
        self._cd2_clients = {}
        self._clients = {}
        self._cd2_url = {}
        
        if self._cd2_confs:
            self._setup_cd2_clients()

        # 初始化上传队列
        if self._enable_queue_management:
            self._upload_queue = UploadQueue(max_concurrent_uploads=self._max_concurrent_uploads)
            logger.info(f"上传队列初始化完成，最大并发数: {self._max_concurrent_uploads}")

        # 初始化统计管理器
        if self._enable_statistics:
            self._statistics = UploadStatistics()
            logger.info("统计管理器初始化完成")

        # 初始化企业级日志系统
        if self._enable_enterprise_logging:
            self._enterprise_logger = EnterpriseLogger(
                plugin_name="cd2upload",
                log_dir=f"logs/enterprise/{self.plugin_name}"
            )
            logger.info("企业级日志系统初始化完成")

        # 初始化配额管理器
        if self._enable_quota_management:
            self._quota_manager = QuotaManager()
            self._quota_manager.set_quota(
                "upload_requests",
                self._quota_upload_limit,
                self._quota_window_hours * 3600
            )
            logger.info(f"配额管理器初始化完成，上传限制: {self._quota_upload_limit}/{self._quota_window_hours}小时")

        # 初始化健康检查器
        if self._enable_health_check:
            self._health_checker = HealthChecker(self)
            logger.info("健康检查器初始化完成")

        # 初始化API处理器
        if self._enable_api:
            self._api_handler = APIHandler(self)
            logger.info("REST API处理器初始化完成")

        # 初始化WebHook管理器
        if self._enable_webhook:
            self._webhook_manager = WebHookManager(self)
            self._webhook_manager.start()
            logger.info("WebHook管理器初始化完成")

        # 补全历史文件
        file_num = int(os.getenv('FULL_RECENT', '0')) if os.getenv('FULL_RECENT', '0').isdigit() else 0
        if file_num:
            recent_files = [transfer_history.dest for transfer_history in
                            TransferHistory.list_by_page(count=file_num, db=get_db())]
            logger.info(f"补全 {len(recent_files)} 个历史文件")
            with lock:
                waiting_process_list = self.get_data('waiting_process_list') or []
                waiting_process_list = waiting_process_list + recent_files
                self.save_data('waiting_process_list', waiting_process_list)

        # 初始化调度器
        self._scheduler = BackgroundScheduler(timezone=settings.TZ)

        if self._onlyonce:
            self._scheduler.add_job(func=self.task, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=10),
                                    name="CloudDrive2智能上传")
            logger.info("CloudDrive2智能上传，立即运行一次")

        if self._cleanlink:
            self._scheduler.add_job(func=self.clean, kwargs={"cleanlink": True}, trigger='date',
                                    run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                    name="清理无效软链接")

        # 定期清理任务
        self._scheduler.add_job(func=self.clean, kwargs={"cleanlink": False}, trigger='interval', 
                                minutes=self._clean_interval, name="定期清理检查")

        # 上传监控任务（如果启用）
        if self._monitor_upload and self._cd2_clients:
            self._scheduler.add_job(func=self.monitor_upload_tasks, trigger='interval',
                                    minutes=self._monitor_interval, name="上传任务监控")

        # Cookie过期检测任务
        if self._enable_cookie_check and self._cd2_clients:
            self._scheduler.add_job(func=self.check_cookie_status, trigger='interval',
                                    minutes=self._cookie_check_interval, name="Cookie过期检测")

        # 上传队列处理任务
        if self._enable_queue_management and self._upload_queue:
            self._scheduler.add_job(func=self.process_upload_queue, trigger='interval',
                                    seconds=self._queue_check_interval, name="上传队列处理")
            
            # 队列状态定期清理
            self._scheduler.add_job(func=self._clean_queue_history, trigger='interval',
                                    hours=1, name="队列历史清理")

        # 统计数据清理任务
        if self._enable_statistics and self._statistics:
            self._scheduler.add_job(func=self._clean_statistics, trigger='interval',
                                    hours=24, name="统计数据清理")

        # 健康检查任务
        if self._enable_health_check and self._health_checker:
            self._scheduler.add_job(func=self._perform_health_check, trigger='interval',
                                    minutes=5, name="健康检查")

        # 企业级日志清理任务
        if self._enable_enterprise_logging:
            self._scheduler.add_job(func=self._clean_enterprise_logs, trigger='interval',
                                    hours=24, name="企业日志清理")

        if self._scheduler.get_jobs():
            self._scheduler.print_jobs()
            self._scheduler.start()

        # 更新配置
        self.update_config({
            'enable': self._enable,
            'cron': self._cron,
            'onlyonce': False,
            'cleanlink': False,
            'monitor_upload': self._monitor_upload,
            'notify_upload': self._notify_upload,
            'upload_retry_count': self._upload_retry_count,
            'cd2_confs': self._cd2_confs,
            'cloud_media_sync': self._cloud_media_sync,
            'monitor_interval': self._monitor_interval,
            'clean_interval': self._clean_interval,
            'enable_cookie_check': self._enable_cookie_check,
            'cookie_check_interval': self._cookie_check_interval,
            'black_dirs': self._black_dirs,
            'upload_timeout': self._upload_timeout,
            'delete_source_after_upload': self._delete_source_after_upload,
            'enable_favorite_notify': self._enable_favorite_notify,
            'notification_type': self._notification_type,
            'notification_channels': self._notification_channels,
            'enable_progress_notify': self._enable_progress_notify,
            'enable_detailed_stats': self._enable_detailed_stats,
            'enable_queue_management': self._enable_queue_management,
            'max_concurrent_uploads': self._max_concurrent_uploads,
            'queue_check_interval': self._queue_check_interval,
            'enable_smart_retry': self._enable_smart_retry,
            'max_retry_attempts': self._max_retry_attempts,
            'retry_base_delay': self._retry_base_delay,
            'retry_max_delay': self._retry_max_delay,
            'enable_jitter': self._enable_jitter,
            'enable_statistics': self._enable_statistics,
            'stats_cleanup_days': self._stats_cleanup_days,
            'enable_performance_monitoring': self._enable_performance_monitoring,
            'enable_enterprise_logging': self._enable_enterprise_logging,
            'enable_distributed_lock': self._enable_distributed_lock,
            'enable_quota_management': self._enable_quota_management,
            'enable_health_check': self._enable_health_check,
            'log_retention_days': self._log_retention_days,
            'quota_upload_limit': self._quota_upload_limit,
            'quota_window_hours': self._quota_window_hours,
            'enable_api': self._enable_api,
            'enable_webhook': self._enable_webhook,
            'api_timeout': self._api_timeout,
            'webhook_secret': self._webhook_secret,
            'softlink_prefix_path': self._softlink_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path
        })

    @eventmanager.register(EventType.TransferComplete)
    def update_waiting_list(self, event: Event):
        transfer_info: TransferInfo = event.event_data.get('transferinfo', {})
        if not transfer_info.file_list_new:
            return
        with lock:
            # 等待转移的文件的链接的完整路径
            waiting_process_list = self.get_data('waiting_process_list') or []
            waiting_process_list = waiting_process_list + transfer_info.file_list_new
            self.save_data('waiting_process_list', waiting_process_list)

        logger.info(f'新入库，加入待转移列表 {transfer_info.file_list_new}')

        # 判断段转移任务开始时间 新剧晚点上传 老剧立马上传
        media_info: MediaInfo = event.event_data.get('mediainfo', {})
        meta: MetaBase = event.event_data.get("meta")

        if media_info:
            is_exist = self._subscribe_oper.exists(tmdbid=media_info.tmdb_id, doubanid=media_info.douban_id,
                                                   season=media_info.season)
            if is_exist:
                if not self._scheduler.get_jobs():
                    logger.info(f'追更剧集,{self._cron}分钟后开始执行任务...')
                try:
                    self._scheduler.remove_all_jobs()
                    self._scheduler.add_job(func=self.task, trigger='date',
                                            kwargs={"media_info": media_info, "meta": meta},
                                            run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(
                                                minutes=self._cron),
                                            name="cd2转移")
                except Exception as err:
                    logger.error(f"定时任务配置错误：{str(err)}")
            else:
                if not self._scheduler.get_jobs():
                    logger.info(f'已完结剧集,立即执行上传任务...')
                self._scheduler.remove_all_jobs()
                self._scheduler.add_job(func=self.task, trigger='date',
                                        run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=5),
                                        name="cd2转移")
            self._scheduler.start()

    def task(self, media_info: MediaInfo = None, meta: MetaBase = None):
        start_time = time.time()
        task_id = str(uuid.uuid4())[:8]
        
        # 记录业务事件
        if self._enterprise_logger:
            self._enterprise_logger.log_business_event(
                "upload_task_started",
                {"task_id": task_id, "media_title": media_info.title_year if media_info else "unknown"},
                file_path=str(len(waiting_process_list)) + " files" if 'waiting_process_list' in locals() else "0 files"
            )
        
        # 使用分布式锁保护关键资源
        lock_resource = "upload_task_execution"
        if self._enable_distributed_lock:
            with DistributedLock(lock_resource, timeout=600):
                return self._execute_task_with_lock(task_id, media_info, meta, start_time)
        else:
            with lock:
                return self._execute_task_with_lock(task_id, media_info, meta, start_time)
    
    def _execute_task_with_lock(self, task_id: str, media_info: MediaInfo = None, meta: MetaBase = None, start_time: float = None):
        """在锁保护下执行任务"""
        try:
            waiting_process_list = self.get_data('waiting_process_list') or []

            if not waiting_process_list:
                logger.info('没有需要转移的媒体文件')
                if self._enterprise_logger:
                    self._enterprise_logger.log_business_event(
                        "upload_task_skipped", 
                        {"task_id": task_id, "reason": "no_files"},
                        status="INFO"
                    )
                return
                
            # 检查配额
            if self._quota_manager and not self._quota_manager.check_quota("upload_requests", len(waiting_process_list)):
                quota_status = self._quota_manager.get_quota_status("upload_requests")
                logger.warning(f"上传请求超出配额限制: {quota_status}")
                if self._enterprise_logger:
                    self._enterprise_logger.log_business_event(
                        "upload_quota_exceeded",
                        {"task_id": task_id, "quota_status": quota_status},
                        status="WARNING"
                    )
                return
                
            logger.info('开始执行智能上传任务，上传完成后将通知Cloud Media Sync处理')
            logger.info(f'待上传文件列表: {waiting_process_list}')
            
            # 记录性能指标
            if self._enterprise_logger:
                self._enterprise_logger.log_performance_metric(
                    "files_in_queue", len(waiting_process_list), "count", {"task_id": task_id}
                )
            
            # 检查是否启用队列管理
            if self._enable_queue_management and self._upload_queue:
                self._add_tasks_to_queue(waiting_process_list, media_info, meta)
                # 清空等待列表，因为已经加入队列
                self.save_data('waiting_process_list', [])
                logger.info(f"已将 {len(waiting_process_list)} 个文件加入上传队列")
                
                if self._enterprise_logger:
                    self._enterprise_logger.log_business_event(
                        "files_queued",
                        {"task_id": task_id, "count": len(waiting_process_list), "queue_management": True}
                    )
            else:
                # 使用传统的直接上传方式
                self._process_upload_directly(waiting_process_list, media_info, meta, start_time)
                
        except Exception as e:
            if self._enterprise_logger:
                self._enterprise_logger.log_error(
                    "task_execution_error", 
                    str(e),
                    context={"task_id": task_id, "media_info": media_info.title_year if media_info else None}
                )
            raise

    def _add_tasks_to_queue(self, file_list: List[str], media_info: MediaInfo = None, meta: MetaBase = None):
        """将文件添加到上传队列"""
        # 确定任务优先级
        priority = UploadPriority.NORMAL
        if media_info:
            # 检查是否为收藏剧集
            favor_data = self.get_data('favor') or {}
            tmdb_id = str(media_info.tmdb_id)
            if favor_data.get(tmdb_id) and media_info.type == MediaType.TV:
                priority = UploadPriority.HIGH
                logger.info(f"收藏剧集检测到，设置为高优先级: {media_info.title_year}")

        # 添加任务到队列
        for file_path in file_list:
            cd2_dest = file_path.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
            task = UploadTask(
                file_path=file_path,
                cd2_dest=cd2_dest,
                priority=priority,
                media_info=media_info,
                meta=meta
            )
            self._upload_queue.add_task(task)

        # 发送开始通知
        if self._enable_progress_notify:
            queue_status = self._upload_queue.get_queue_status()
            self._send_notification(
                title="文件加入上传队列",
                text=f"已加入 {len(file_list)} 个文件到上传队列\n队列状态: {queue_status['queued']} 待上传, {queue_status['active']} 处理中"
            )

    def _process_upload_directly(self, waiting_process_list: List[str], media_info: MediaInfo = None, meta: MetaBase = None, start_time: float = None):
        """直接处理上传（传统方式）"""
        processed_list = self.get_data('processed_list') or []
        
        # 初始化统计信息
        upload_stats = {
            'total': len(waiting_process_list),
            'success': 0,
            'failed': 0,
            'start_time': start_time,
            'failed_files': []
        }
        
        # 发送开始上传通知
        if self._enable_progress_notify:
            self._send_notification(
                title="CloudDrive2上传开始",
                text=f"开始上传 {upload_stats['total']} 个文件"
            )
        
        process_list = waiting_process_list.copy()
        for index, softlink_source in enumerate(waiting_process_list):
            # 链接目录前缀 替换为 cd2挂载前缀
            cd2_dest = softlink_source.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
            
            # 记录当前进度
            current_progress = index + 1
            logger.info(f'【{current_progress}/{upload_stats["total"]}】处理文件: {softlink_source}')
            
            if self._upload_file_with_retry(softlink_source=softlink_source, cd2_dest=cd2_dest):
                process_list.remove(softlink_source)
                processed_list.append(softlink_source)
                upload_stats['success'] += 1
                logger.info(f'【{current_progress}/{upload_stats["total"]}】上传成功: {softlink_source}')
                
                # 发送进度通知
                if self._enable_progress_notify and current_progress % 5 == 0:  # 每5个文件通知一次
                    self._send_notification(
                        title="CloudDrive2上传进度",
                        text=f"已完成 {current_progress}/{upload_stats['total']} 个文件"
                    )
            else:
                upload_stats['failed'] += 1
                upload_stats['failed_files'].append(softlink_source)
                logger.error(f'【{current_progress}/{upload_stats["total"]}】上传失败: {softlink_source}')
                continue
                
        # 完成统计
        end_time = time.time()
        upload_stats['end_time'] = end_time
        upload_stats['duration'] = int(end_time - start_time)
        
        # 保存统计数据
        if self._enable_detailed_stats:
            self._save_upload_stats(upload_stats, media_info, meta)
        
        logger.info(f"上传任务完成 - 成功: {upload_stats['success']}, 失败: {upload_stats['failed']}, 用时: {upload_stats['duration']}秒")
        self.save_data('waiting_process_list', process_list)
        self.save_data('processed_list', processed_list)

        # 发送完成通知
        self._send_upload_completion_notification(upload_stats, media_info, meta)

    def _classify_error(self, error: Exception) -> ErrorType:
        """分类错误类型"""
        error_str = str(error).lower()
        
        if "permission" in error_str or "access" in error_str:
            return ErrorType.PERMISSION_ERROR
        elif "no space" in error_str or "disk full" in error_str:
            return ErrorType.DISK_FULL
        elif "not found" in error_str or "no such file" in error_str:
            return ErrorType.FILE_NOT_FOUND
        elif "network" in error_str or "timeout" in error_str or "connection" in error_str:
            return ErrorType.NETWORK_ERROR
        elif "temporary" in error_str or "busy" in error_str:
            return ErrorType.TEMPORARY_ERROR
        else:
            return ErrorType.UNKNOWN_ERROR

    def _is_retryable_error(self, error_type: ErrorType) -> bool:
        """判断错误是否可重试"""
        non_retryable = {ErrorType.PERMISSION_ERROR, ErrorType.FILE_NOT_FOUND}
        return error_type not in non_retryable

    def _calculate_retry_delay(self, attempt: int) -> float:
        """计算重试延迟时间（智能退避算法）"""
        if not self._enable_smart_retry:
            return 2 ** attempt  # 简单指数退避
            
        base_delay = self._retry_base_delay
        max_delay = self._retry_max_delay
        
        # 指数退避
        delay = min(base_delay * (2 ** attempt), max_delay)
        
        # 添加抖动
        if self._enable_jitter:
            jitter = random.uniform(0, delay * 0.1)
            delay += jitter
            
        return delay

    def _upload_file_with_retry(self, softlink_source: str = None, cd2_dest: str = None) -> bool:
        """带智能重试机制的文件上传"""
        max_attempts = self._max_retry_attempts if self._enable_smart_retry else self._upload_retry_count
        
        for attempt in range(max_attempts):
            try:
                if self._upload_file(softlink_source, cd2_dest):
                    return True
                    
                logger.warning(f"上传失败，第 {attempt + 1}/{max_attempts} 次重试: {softlink_source}")
                
                # 如果不是最后一次尝试，等待后重试
                if attempt < max_attempts - 1:
                    delay = self._calculate_retry_delay(attempt)
                    logger.info(f"等待 {delay:.1f} 秒后重试")
                    time.sleep(delay)
                    
            except Exception as e:
                error_type = self._classify_error(e)
                logger.error(f"上传异常，第 {attempt + 1}/{max_attempts} 次重试: {e} (错误类型: {error_type.value})")
                
                # 检查是否可重试
                if not self._is_retryable_error(error_type):
                    logger.error(f"错误类型 {error_type.value} 不可重试，放弃上传: {softlink_source}")
                    return False
                
                # 如果不是最后一次尝试，等待后重试
                if attempt < max_attempts - 1:
                    delay = self._calculate_retry_delay(attempt)
                    
                    # 对于磁盘满错误，使用更长的延迟
                    if error_type == ErrorType.DISK_FULL:
                        delay *= 3
                        logger.warning(f"磁盘空间不足，延长等待时间到 {delay:.1f} 秒")
                    
                    logger.info(f"等待 {delay:.1f} 秒后重试")
                    time.sleep(delay)
                    
        return False

    def _upload_file(self, softlink_source: str = None, cd2_dest: str = None) -> bool:
        """基础文件上传方法"""
        file_size = 0
        start_time = time.time()
        
        try:
            # 获取文件大小用于统计
            if os.path.exists(softlink_source):
                real_source = os.readlink(softlink_source) if os.path.islink(softlink_source) else softlink_source
                if os.path.exists(real_source):
                    file_size = os.path.getsize(real_source)
                    
            # 记录上传尝试
            if self._statistics:
                self._statistics.record_upload_attempt(softlink_source, file_size)
                
            cd2_dest_folder, cd2_dest_file_name = os.path.split(cd2_dest)

            if not os.path.exists(cd2_dest_folder):
                os.makedirs(cd2_dest_folder)
                logger.info(f'创建文件夹 {cd2_dest_folder}')

            real_source = os.readlink(softlink_source)
            logger.debug(f'源文件路径 {real_source}')

            if not os.path.exists(cd2_dest):
                # 将文件上传到当前文件夹 同步
                shutil.copy2(softlink_source, cd2_dest, follow_symlinks=True)
                
                # 如果启用删除源文件功能
                if self._delete_source_after_upload:
                    try:
                        os.remove(real_source)
                        logger.info(f"已删除源文件: {real_source}")
                    except Exception as e:
                        logger.error(f"删除源文件失败: {e}")
            else:
                logger.info(f'{cd2_dest_file_name} 已存在 {cd2_dest}')
                
            # 记录成功结果
            duration = time.time() - start_time
            if self._statistics:
                self._statistics.record_upload_result(softlink_source, True, duration, file_size)
            
            # 触发WebHook事件
            if self._webhook_manager:
                self._webhook_manager.trigger_webhook("upload_success", {
                    "file_path": softlink_source,
                    "cd2_dest": cd2_dest,
                    "file_size": file_size,
                    "duration": duration
                })
                
            return True
        except Exception as e:
            # 记录失败结果
            duration = time.time() - start_time
            if self._statistics:
                error_type = self._classify_error(e).value
                self._statistics.record_upload_result(softlink_source, False, duration, file_size, error_type)
            
            # 触发WebHook事件
            if self._webhook_manager:
                self._webhook_manager.trigger_webhook("upload_failed", {
                    "file_path": softlink_source,
                    "cd2_dest": cd2_dest,
                    "error": str(e),
                    "error_type": self._classify_error(e).value,
                    "duration": duration
                })
                
            logger.error(f"上传文件失败: {e}")
            return False

    def _save_upload_stats(self, stats: Dict, media_info: MediaInfo = None, meta: MetaBase = None):
        """保存上传统计数据"""
        upload_history = self.get_data('upload_history') or []
        
        upload_record = {
            'timestamp': datetime.now().isoformat(),
            'total_files': stats['total'],
            'success_count': stats['success'],
            'failed_count': stats['failed'],
            'duration': stats['duration'],
            'failed_files': stats['failed_files'],
            'media_title': media_info.title_year if media_info else "未知",
            'media_type': media_info.type.value if media_info else "unknown"
        }
        
        upload_history.append(upload_record)
        
        # 只保留最近100条记录
        if len(upload_history) > 100:
            upload_history = upload_history[-100:]
            
        self.save_data('upload_history', upload_history)

    def _send_upload_completion_notification(self, stats: Dict, media_info: MediaInfo = None, meta: MetaBase = None):
        """发送上传完成通知"""
        title = "CloudDrive2上传完成"
        
        if stats['failed'] == 0:
            text = f"✅ 全部上传成功！\n文件数量: {stats['success']}/{stats['total']}\n用时: {stats['duration']}秒"
        else:
            text = f"⚠️ 部分上传失败\n成功: {stats['success']}/{stats['total']}\n失败: {stats['failed']} 个\n用时: {stats['duration']}秒"
        
        # 如果是收藏的剧集，添加额外信息
        if media_info:
            favor_data = self.get_data('favor') or {}
            tmdb_id = str(media_info.tmdb_id)
            
            if favor_data.get(tmdb_id) and media_info.type == MediaType.TV:
                title = f"{media_info.title_year} {meta.episodes if meta else ''}"
                text += f"\n\n📺 收藏剧集更新完成"
                
                self._send_notification(
                    title=title,
                    text=text,
                    image=media_info.get_message_image() if hasattr(media_info, 'get_message_image') else None
                )
                return
        
        # 发送标准完成通知
        self._send_notification(title=title, text=text)

    def clean(self, cleanlink: bool = False):
        with lock:
            waiting_process_list = self.get_data('processed_list') or []
            processed_list = waiting_process_list.copy()
            logger.info(f"已处理列表：{processed_list}")
            logger.debug(f"cleanlink {cleanlink}")

            for file in waiting_process_list:
                if not os.path.islink(file):
                    processed_list.remove(file)
                    logger.info(f"软链接符号不存在 {file}")
                    continue
                if cleanlink and os.path.islink(file):
                    try:
                        target_file = os.readlink(file)
                        os.remove(target_file)
                        logger.info(f"清除源文件 {target_file}")
                    except FileNotFoundError:
                        logger.warning(f"无法删除 {file} 指向的目标文件，目标文件不存在")
                    except OSError as e:
                        logger.error(f"删除 {file} 目标文件失败: {e}")

                if os.path.islink(file) and not os.path.exists(file):
                    os.remove(file)
                    processed_list.remove(file)
                    logger.info(f"删除本地链接文件 {file}")

                    # 构造 CloudDrive2 目标路径
                    cd2_dest = file.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
                    strm_file_path = os.path.splitext(file)[0] + '.strm'

                    # 通知Cloud Media Sync处理文件
                    if self._cloud_media_sync:
                        file_info = {
                            "softlink_path": file,
                            "cd2_path": cd2_dest,
                            "strm_path": strm_file_path
                        }
                        self._notify_cloud_media_sync(file_info)
                    else:
                        logger.info(f"未启用Cloud Media Sync，跳过文件处理：{file}")

                else:
                    logger.debug(f"{file} 未失效，跳过")

            self.save_data('processed_list', processed_list)

    def _setup_cd2_clients(self):
        """设置CloudDrive2客户端"""
        if not self._cd2_confs:
            return
            
        for cd2_conf in self._cd2_confs.split("\n"):
            if not cd2_conf.strip():
                continue
            try:
                parts = cd2_conf.strip().split("#")
                if len(parts) != 4:
                    logger.error(f"CloudDrive2配置格式错误：{cd2_conf}")
                    continue
                    
                cd2_name, cd2_url, username, password = parts
                _cd2_client = CloudDriveClient(cd2_url, username, password)
                _client = Client(cd2_url, username, password)
                
                if _cd2_client and _client:
                    self._cd2_clients[cd2_name] = _cd2_client
                    self._clients[cd2_name] = _client
                    self._cd2_url[cd2_name] = cd2_url
                    logger.info(f"CloudDrive2客户端连接成功：{cd2_name}")
                else:
                    logger.error(f"CloudDrive2客户端连接失败：{cd2_name}")
            except Exception as e:
                logger.error(f"设置CloudDrive2客户端失败：{e}")

    def monitor_upload_tasks(self):
        """监控上传任务状态"""
        if not self._cd2_clients:
            return
            
        for cd2_name, cd2_client in self._cd2_clients.items():
            try:
                # 获取上传任务列表
                upload_tasklist = cd2_client.upload_tasklist.list(page=0, page_size=20, filter="")
                if not upload_tasklist:
                    continue
                
                failed_tasks = []
                for task in upload_tasklist:
                    if task.get("status") == "FatalError":
                        failed_tasks.append({
                            "name": task.get("name", "未知文件"),
                            "error": task.get("errorMessage", "未知错误"),
                            "cd2_name": cd2_name
                        })
                
                if failed_tasks and self._notify_upload:
                    self._notify_upload_failures(failed_tasks)
                    
            except Exception as e:
                logger.error(f"监控{cd2_name}上传任务失败：{e}")

    def _notify_upload_failures(self, failed_tasks: List[Dict]):
        """通知上传失败任务"""
        if not failed_tasks:
            return
            
        title = f"CloudDrive2上传失败通知"
        error_details = []
        for task in failed_tasks:
            error_details.append(f"【{task['cd2_name']}】{task['name']}: {task['error']}")
        
        text = f"发现{len(failed_tasks)}个上传失败任务：\n" + "\n".join(error_details)
        
        self._send_notification(title=title, text=text)

    def process_upload_queue(self):
        """处理上传队列"""
        if not self._upload_queue:
            return

        # 检查并处理待上传任务
        tasks_started = 0
        while True:
            task = self._upload_queue.get_next_task()
            if not task:
                break
                
            # 在新线程中处理任务以支持并发
            thread = threading.Thread(
                target=self._process_queue_task,
                args=(task,),
                daemon=True
            )
            thread.start()
            tasks_started += 1
            
        # 更新并发峰值统计
        if self._statistics and tasks_started > 0:
            current_concurrent = len(self._upload_queue.active_uploads) if self._upload_queue else 0
            self._statistics.update_concurrent_peak(current_concurrent)
            
        if tasks_started > 0:
            logger.debug(f"启动了 {tasks_started} 个上传任务")

    def _process_queue_task(self, task: UploadTask):
        """处理单个队列任务"""
        logger.info(f"开始处理队列任务: {task.file_path} (优先级: {task.priority.name}, 重试次数: {task.retry_count})")
        
        success = False
        error_type = None
        try:
            success = self._upload_file(task.file_path, task.cd2_dest)
            
            if success:
                logger.info(f"队列任务上传成功: {task.file_path}")
                self._handle_successful_upload(task)
            else:
                logger.error(f"队列任务上传失败: {task.file_path}")
                task.last_error = "上传失败"
                task.error_type = ErrorType.UNKNOWN_ERROR
                self._handle_failed_upload(task)
                
        except Exception as e:
            error_type = self._classify_error(e)
            task.last_error = str(e)
            task.error_type = error_type
            logger.error(f"队列任务处理异常: {task.file_path}, 错误: {e} (类型: {error_type.value})")
            self._handle_failed_upload(task)
            
        finally:
            # 标记任务完成
            self._upload_queue.mark_task_completed(task, success)

    def _handle_successful_upload(self, task: UploadTask):
        """处理上传成功的任务"""
        with lock:
            # 更新processed_list
            processed_list = self.get_data('processed_list') or []
            if task.file_path not in processed_list:
                processed_list.append(task.file_path)
                self.save_data('processed_list', processed_list)
                
            # 从waiting_process_list中移除（如果存在）
            waiting_list = self.get_data('waiting_process_list') or []
            if task.file_path in waiting_list:
                waiting_list.remove(task.file_path)
                self.save_data('waiting_process_list', waiting_list)

        # 通知Cloud Media Sync处理文件
        if self._cloud_media_sync:
            cd2_dest = task.file_path.replace(self._softlink_prefix_path, self._cd_mount_prefix_path)
            strm_file_path = os.path.splitext(task.file_path)[0] + '.strm'
            
            file_info = {
                "softlink_path": task.file_path,
                "cd2_path": cd2_dest,
                "strm_path": strm_file_path,
                "media_type": task.media_info.type.value if task.media_info else "unknown"
            }
            self._notify_cloud_media_sync(file_info)

    def _handle_failed_upload(self, task: UploadTask):
        """智能处理上传失败的任务"""
        max_attempts = self._max_retry_attempts if self._enable_smart_retry else self._upload_retry_count
        
        # 检查错误是否可重试
        if task.error_type and not self._is_retryable_error(task.error_type):
            logger.error(f"任务因不可重试错误失败: {task.file_path} (错误类型: {task.error_type.value})")
            
            # 发送失败通知
            if self._notify_upload:
                self._send_notification(
                    title="CloudDrive2队列上传失败",
                    text=f"文件上传失败: {os.path.basename(task.file_path)}\n错误类型: {task.error_type.value}\n错误详情: {task.last_error}"
                )
            return
        
        # 如果还有重试机会，将任务重新加入队列
        if task.retry_count < max_attempts:
            # 使用智能重试参数
            self._upload_queue.retry_task(
                task, 
                max_attempts=self._max_retry_attempts,
                base_delay=self._retry_base_delay,
                max_delay=self._retry_max_delay,
                enable_jitter=self._enable_jitter
            )
            
            retry_time = time.strftime('%H:%M:%S', time.localtime(task.next_retry_time))
            logger.info(f"任务重新加入队列进行重试: {task.file_path} (重试次数: {task.retry_count}, 下次重试: {retry_time})")
        else:
            logger.error(f"任务重试次数已达上限，放弃处理: {task.file_path}")
            
            # 发送详细的失败通知
            if self._notify_upload:
                error_info = f"错误类型: {task.error_type.value if task.error_type else '未知'}"
                if task.last_error:
                    error_info += f"\n错误详情: {task.last_error}"
                    
                self._send_notification(
                    title="CloudDrive2队列上传失败",
                    text=f"文件上传失败: {os.path.basename(task.file_path)}\n重试 {task.retry_count} 次后仍然失败\n{error_info}"
                )

    def _clean_queue_history(self):
        """清理队列历史记录"""
        if not self._upload_queue:
            return
            
        try:
            self._upload_queue.clear_completed_history()
            logger.debug("队列历史记录清理完成")
        except Exception as e:
            logger.error(f"清理队列历史记录失败: {e}")

    def get_queue_status(self) -> Dict:
        """获取队列状态（用于API调用）"""
        if not self._upload_queue:
            return {"error": "队列管理未启用"}
            
        return self._upload_queue.get_queue_status()

    def _clean_statistics(self):
        """清理统计数据"""
        if not self._statistics:
            return
            
        try:
            self._statistics.cleanup_old_data(keep_days=self._stats_cleanup_days)
            logger.debug(f"统计数据清理完成，保留 {self._stats_cleanup_days} 天数据")
        except Exception as e:
            logger.error(f"清理统计数据失败: {e}")

    def get_statistics_dashboard(self) -> Dict:
        """获取统计仪表板数据"""
        if not self._statistics:
            return {"error": "统计功能未启用"}
            
        try:
            dashboard_data = {
                "performance_summary": self._statistics.get_performance_summary(),
                "daily_summary": self._statistics.get_daily_summary(days=7),
                "error_analysis": self._statistics.get_error_analysis(),
                "queue_status": self.get_queue_status() if self._upload_queue else {"error": "队列未启用"},
                "file_type_stats": dict(list(self._statistics.file_type_stats.items())[:10]),  # 前10种文件类型
                "hourly_trend": dict(list(self._statistics.hourly_stats.items())[-24:])  # 最近24小时
            }
            return dashboard_data
        except Exception as e:
            logger.error(f"获取统计数据失败: {e}")
            return {"error": f"获取统计数据失败: {str(e)}"}

    def get_performance_metrics(self) -> Dict:
        """获取性能指标"""
        if not self._statistics:
            return {"error": "统计功能未启用"}
            
        try:
            metrics = self._statistics.get_performance_summary()
            
            # 添加队列相关指标
            if self._upload_queue:
                queue_status = self._upload_queue.get_queue_status()
                metrics.update({
                    "queue_length": queue_status.get("queued", 0),
                    "active_uploads": queue_status.get("active", 0),
                    "queue_completion_rate": round(
                        queue_status.get("stats", {}).get("total_success", 0) / 
                        max(queue_status.get("stats", {}).get("total_processed", 1), 1) * 100, 2
                    )
                })
                
            return metrics
        except Exception as e:
            logger.error(f"获取性能指标失败: {e}")
            return {"error": f"获取性能指标失败: {str(e)}"}

    def get_error_report(self) -> Dict:
        """获取错误报告"""
        if not self._statistics:
            return {"error": "统计功能未启用"}
            
        try:
            error_analysis = self._statistics.get_error_analysis()
            
            # 获取队列中的失败任务详情
            queue_failures = []
            if self._upload_queue:
                for failed_task in self._upload_queue.failed_uploads[-10:]:  # 最近10个失败任务
                    queue_failures.append({
                        "file": os.path.basename(failed_task.file_path),
                        "error_type": failed_task.error_type.value if failed_task.error_type else "unknown",
                        "last_error": failed_task.last_error,
                        "retry_count": failed_task.retry_count,
                        "created_time": time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(failed_task.created_time))
                    })
                    
            return {
                "error_statistics": error_analysis,
                "recent_failures": queue_failures,
                "total_error_types": len(error_analysis),
                "most_common_error": max(error_analysis.items(), key=lambda x: x[1]) if error_analysis else None
            }
        except Exception as e:
            logger.error(f"获取错误报告失败: {e}")
            return {"error": f"获取错误报告失败: {str(e)}"}

    def _perform_health_check(self):
        """执行健康检查"""
        if not self._health_checker:
            return
            
        try:
            health_status = self._health_checker.check_health()
            
            # 记录健康检查结果
            if self._enterprise_logger:
                self._enterprise_logger.log_performance_metric(
                    "health_check_status", 
                    1 if health_status["overall"] == "healthy" else 0,
                    "bool",
                    {"components": health_status.get("failed_components", [])}
                )
            
            # 如果健康状态异常，尝试自愈
            if health_status["overall"] != "healthy":
                self._attempt_self_healing(health_status)
                
        except Exception as e:
            logger.error(f"健康检查失败: {e}")
            if self._enterprise_logger:
                self._enterprise_logger.log_error("health_check_failed", str(e))

    def _attempt_self_healing(self, health_status: Dict):
        """尝试自愈机制"""
        failed_components = health_status.get("failed_components", [])
        
        for component in failed_components:
            try:
                if component == "queue_health":
                    self._heal_queue_issues()
                elif component == "cd2_clients_health":
                    self._heal_cd2_client_issues()
                elif component == "storage_health":
                    self._heal_storage_issues()
                    
                if self._enterprise_logger:
                    self._enterprise_logger.log_business_event(
                        "self_healing_attempted",
                        {"component": component, "status": "attempted"}
                    )
                    
            except Exception as e:
                logger.error(f"自愈组件 {component} 失败: {e}")
                if self._enterprise_logger:
                    self._enterprise_logger.log_error(
                        "self_healing_failed", 
                        str(e), 
                        context={"component": component}
                    )

    def _heal_queue_issues(self):
        """修复队列问题"""
        if self._upload_queue:
            # 清理僵死任务
            current_time = time.time()
            for task_path, task in list(self._upload_queue.active_uploads.items()):
                if current_time - task.created_time > 3600:  # 1小时超时
                    logger.warning(f"清理超时任务: {task_path}")
                    self._upload_queue.mark_task_completed(task, False)

    def _heal_cd2_client_issues(self):
        """修复CD2客户端问题"""
        if self._cd2_confs:
            logger.info("尝试重新连接CloudDrive2客户端")
            self._setup_cd2_clients()

    def _heal_storage_issues(self):
        """修复存储问题"""
        # 清理临时文件和失效链接
        try:
            self.clean(cleanlink=False)
            logger.info("执行存储清理操作")
        except Exception as e:
            logger.error(f"存储清理失败: {e}")

    def _clean_enterprise_logs(self):
        """清理企业级日志"""
        if not self._enterprise_logger:
            return
            
        try:
            log_dir = self._enterprise_logger.log_dir
            cutoff_date = datetime.now() - timedelta(days=self._log_retention_days)
            
            cleaned_files = 0
            for log_file in log_dir.glob("*.log*"):
                if log_file.stat().st_mtime < cutoff_date.timestamp():
                    log_file.unlink()
                    cleaned_files += 1
                    
            if cleaned_files > 0:
                logger.info(f"清理了 {cleaned_files} 个过期日志文件")
                
                if self._enterprise_logger:
                    self._enterprise_logger.log_business_event(
                        "logs_cleaned",
                        {"cleaned_files": cleaned_files, "retention_days": self._log_retention_days}
                    )
                    
        except Exception as e:
            logger.error(f"清理企业日志失败: {e}")

    def get_enterprise_status(self) -> Dict:
        """获取企业级状态总览"""
        try:
            status = {
                "plugin_info": {
                    "name": self.plugin_name,
                    "version": self.plugin_version,
                    "session_id": self._enterprise_logger.session_id if self._enterprise_logger else "N/A"
                },
                "health": self._health_checker.check_health() if self._health_checker else {"status": "disabled"},
                "quota_status": {},
                "performance_summary": self.get_performance_metrics(),
                "enterprise_features": {
                    "enterprise_logging": self._enable_enterprise_logging,
                    "distributed_lock": self._enable_distributed_lock,
                    "quota_management": self._enable_quota_management,
                    "health_check": self._enable_health_check
                }
            }
            
            # 添加配额状态
            if self._quota_manager:
                status["quota_status"] = self._quota_manager.get_quota_status("upload_requests")
            
            return status
            
        except Exception as e:
            return {"error": f"获取企业状态失败: {str(e)}"}

    def get_audit_trail(self, hours: int = 24) -> Dict:
        """获取审计跟踪"""
        if not self._enterprise_logger:
            return {"error": "企业级日志未启用"}
            
        try:
            # 这里应该从日志文件中解析审计记录
            # 简化实现，返回基本信息
            return {
                "time_range": f"最近 {hours} 小时",
                "log_files": {
                    "business": str(self._enterprise_logger.log_dir / f"{self._enterprise_logger.plugin_name}_business.log"),
                    "audit": str(self._enterprise_logger.log_dir / f"{self._enterprise_logger.plugin_name}_audit.log"),
                    "performance": str(self._enterprise_logger.log_dir / f"{self._enterprise_logger.plugin_name}_performance.log"),
                    "error": str(self._enterprise_logger.log_dir / f"{self._enterprise_logger.plugin_name}_error.log")
                },
                "session_id": self._enterprise_logger.session_id
            }
            
        except Exception as e:
            return {"error": f"获取审计跟踪失败: {str(e)}"}

    def handle_api_request(self, path: str, method: str = "GET", params: Dict = None, headers: Dict = None) -> Dict:
        """处理API请求（供外部调用）"""
        if not self._api_handler:
            return {"error": "API功能未启用", "code": 503}
            
        return self._api_handler.handle_request(path, method, params, headers)

    def register_webhook(self, event_type: str, url: str, secret: str = None, headers: Dict = None) -> str:
        """注册WebHook（供外部调用）"""
        if not self._webhook_manager:
            raise RuntimeError("WebHook功能未启用")
            
        return self._webhook_manager.register_webhook(event_type, url, secret, headers)

    def unregister_webhook(self, webhook_id: str) -> bool:
        """注销WebHook"""
        if not self._webhook_manager:
            return False
            
        return self._webhook_manager.unregister_webhook(webhook_id)

    def list_webhooks(self) -> List[Dict]:
        """列出WebHook"""
        if not self._webhook_manager:
            return []
            
        return self._webhook_manager.list_webhooks()

    def stop_service(self):
        """停止服务"""
        try:
            if self._scheduler:
                self._scheduler.shutdown()
                self._scheduler = None
            
            if self._webhook_manager:
                self._webhook_manager.stop()
                self._webhook_manager = None
                
            logger.info("CloudDrive2智能上传服务已停止")
        except Exception as e:
            logger.error(f"停止服务时出错: {e}")

    def _send_notification(self, title: str, text: str = None, image: str = None):
        """发送通知，支持通知渠道选择"""
        # 获取通知类型
        try:
            mtype = NotificationType.__getitem__(self._notification_type) if self._notification_type else NotificationType.Plugin
        except (KeyError, AttributeError):
            mtype = NotificationType.Plugin
        
        # 如果指定了通知渠道
        if self._notification_channels:
            channels = [ch.strip() for ch in self._notification_channels.split(",") if ch.strip()]
            for channel in channels:
                try:
                    self.post_message(
                        title=title,
                        text=text,
                        image=image,
                        mtype=mtype,
                        channel=channel
                    )
                except Exception as e:
                    logger.error(f"发送通知到渠道 {channel} 失败: {e}")
        else:
            # 使用默认通知方式
            self.post_message(
                title=title,
                text=text,
                image=image,
                mtype=mtype
            )

    def _notify_cloud_media_sync(self, file_info: Dict):
        """通知Cloud Media Sync处理STRM生成"""
        if not self._cloud_media_sync:
            logger.info("未启用Cloud Media Sync通知，跳过")
            return
            
        # 构造通知数据
        event_data = {
            "source": "CloudDrive2智能上传",
            "action": "strm_generate_request",
            "file_path": file_info.get("softlink_path"),
            "cloud_path": file_info.get("cd2_path"),
            "strm_path": file_info.get("strm_path"),
            "upload_completed": True,
            "media_type": file_info.get("media_type", "unknown"),
            "timestamp": datetime.now().isoformat()
        }
        
        # 发送插件通信事件
        event = Event(EventType.PluginAction, {
            "plugin_name": "CloudMediaSync",
            "action": "handle_upload_completion",
            "data": event_data
        })
        eventmanager.send_event(event)
        
        logger.info(f"已通知Cloud Media Sync处理文件: {file_info.get('softlink_path')}")

    def check_cookie_status(self):
        """检查CloudDrive2 Cookie状态"""
        if not self._cd2_clients:
            return
            
        for cd2_name, cd2_client in self._cd2_clients.items():
            try:
                logger.info(f"开始检查 {cd2_name} Cookie状态")
                fs = cd2_client.fs
                if not fs:
                    logger.error(f"{cd2_name} CloudDrive2连接失败")
                    continue

                # 获取目录列表并检查是否可访问
                for dir_item in fs.listdir():
                    if dir_item and dir_item not in self._black_dirs.split(","):
                        try:
                            cloud_files = fs.listdir(dir_item)
                            if cloud_files is None:
                                error_msg = f"云盘 {dir_item} Cookie可能已过期"
                                logger.warning(error_msg)
                                if self._notify_upload:
                                    self._send_notification(
                                        title=f"CloudDrive2 Cookie警告",
                                        text=f"【{cd2_name}】{error_msg}"
                                    )
                        except Exception as err:
                            error_msg = f"云盘 {dir_item} 访问异常"
                            logger.error(f"{error_msg}: {err}")
                            if "429" in str(err):
                                error_msg = f"云盘 {dir_item} 访问频率过高，请稍后再试"
                            if self._notify_upload:
                                self._send_notification(
                                    title=f"CloudDrive2 Cookie错误",
                                    text=f"【{cd2_name}】{error_msg}: {err}"
                                )
                            
            except Exception as e:
                logger.error(f"检查{cd2_name} Cookie状态失败：{e}")

    @eventmanager.register(EventType.WebhookMessage)
    def record_favor(self, event: Event):
        """
        记录favorite剧集，支持收藏更新通知
        """
        # 检查是否启用收藏通知功能
        if not self._enable_favorite_notify:
            return
            
        event_info: WebhookEventInfo = event.event_data
        # 只处理剧集喜爱事件
        if event_info.event != "item.rate" or event_info.item_type != "TV":
            return
        if event_info.channel != "emby":
            logger.info("目前只支持Emby服务端")
            return
        title = event_info.item_name
        tmdb_id = event_info.tmdb_id
        if title.count(" S"):
            logger.info("只处理喜爱整季，单集喜爱不处理")
            return
        try:
            meta = MetaInfo(title)
            mediainfo: MediaInfo = self.chain.recognize_media(meta=meta, tmdbid=tmdb_id, mtype=MediaType.TV)
            # 存储历史记录
            favor: Dict = self.get_data('favor') or {}
            if favor.get(tmdb_id):
                favor.pop(tmdb_id)
                logger.info(f"{mediainfo.title_year} 取消更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 取消更新通知", text=None, image=mediainfo.get_message_image()))
            else:
                favor[tmdb_id] = {
                    "title": title,
                    "type": mediainfo.type.value,
                    "year": mediainfo.year,
                    "poster": mediainfo.get_poster_image(),
                    "overview": mediainfo.overview,
                    "tmdbid": mediainfo.tmdb_id,
                    "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                logger.info(f"{mediainfo.title_year} 加入更新通知")
                self.chain.post_message(Notification(
                    mtype=NotificationType.Plugin,
                    title=f"{mediainfo.title_year} 加入更新通知", text=None, image=mediainfo.get_message_image()))
            self.save_data('favor', favor)
        except Exception as e:
            logger.error(str(e))

    def get_state(self) -> bool:
        return self._enable

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
                'content': [
                    # 企业级Header
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4 enterprise-header-card'
                        },
                        'content': [
                            {
                                'component': 'VCardItem',
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'd-flex align-center'
                                        },
                                        'content': [
                                            {
                                                'component': 'VIcon',
                                                'props': {
                                                    'icon': 'mdi-cloud-upload',
                                                    'class': 'me-3',
                                                    'color': 'primary',
                                                    'size': 'large'
                                                }
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'text-h5'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'span',
                                                        'text': 'CloudDrive2 智能上传'
                                                    },
                                                    {
                                                        'component': 'VChip',
                                                        'props': {
                                                            'size': 'small',
                                                            'color': 'success',
                                                            'variant': 'flat',
                                                            'class': 'ms-3'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VIcon',
                                                                'props': {
                                                                    'icon': 'mdi-crown',
                                                                    'start': True,
                                                                    'size': 'small'
                                                                }
                                                            },
                                                            {
                                                                'component': 'span',
                                                                'text': 'Enterprise'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VCardSubtitle',
                                        'props': {
                                            'class': 'mt-2 text-medium-emphasis'
                                        },
                                        'content': [
                                            {
                                                'component': 'span',
                                                'text': '企业级文件上传管理系统 | 队列管理 | 健康监控 | API集成 | 实时统计'
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    
                    # 快速操作面板
                    {
                        'component': 'VRow',
                        'props': {
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'enable',
                                            'label': '启用插件',
                                            'color': 'primary',
                                            'hide-details': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'onlyonce',
                                            'label': '立即运行一次',
                                            'color': 'warning',
                                            'hide-details': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'cleanlink',
                                            'label': '立即清理',
                                            'color': 'error',
                                            'hide-details': True
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 3
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '延迟上传（分钟）',
                                            'placeholder': '20',
                                            'variant': 'outlined',
                                            'type': 'number',
                                            'hide-details': True
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    
                    # 基本路径配置
                    {
                        'component': 'VRow',
                        'props': {
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'softlink_prefix_path',
                                            'label': '本地软链接路径前缀',
                                            'placeholder': '/strm/',
                                            'variant': 'outlined',
                                            'hide-details': True,
                                            'prepend-inner-icon': 'mdi-folder-open'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'cd_mount_prefix_path',
                                            'label': 'CloudDrive2挂载路径前缀',
                                            'placeholder': '/CloudNAS/115/emby/',
                                            'variant': 'outlined',
                                            'hide-details': True,
                                            'prepend-inner-icon': 'mdi-cloud'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    
                    # 配置面板组
                    {
                        'component': 'VExpansionPanels',
                        'props': {
                            'multiple': True,
                            'variant': 'accordion',
                            'class': 'enterprise-panels'
                        },
                        'content': [
                            # 监控配置
                            {
                                'component': 'VExpansionPanel',
                                'props': {
                                    'value': 'monitoring'
                                },
                                'content': [
                                    {
                                        'component': 'VExpansionPanelTitle',
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'd-flex align-center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VIcon',
                                                        'props': {
                                                            'icon': 'mdi-monitor',
                                                            'class': 'me-3',
                                                            'color': 'info'
                                                        }
                                                    },
                                                    {
                                                        'component': 'span',
                                                        'props': {
                                                            'class': 'text-h6'
                                                        },
                                                        'text': '监控配置'
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VExpansionPanelText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'monitor_upload',
                                                                'label': '开启上传监控',
                                                                'color': 'success',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'notify_upload',
                                                                'label': '上传通知',
                                                                'color': 'warning',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_favorite_notify',
                                                                'label': '收藏通知',
                                                                'color': 'error',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 6},
                                                        'content': [{
                                                            'component': 'VTextField',
                                                            'props': {
                                                                'model': 'monitor_interval',
                                                                'label': '监控间隔（分钟）',
                                                                'placeholder': '10',
                                                                'variant': 'outlined',
                                                                'type': 'number',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 6},
                                                        'content': [{
                                                            'component': 'VTextField',
                                                            'props': {
                                                                'model': 'clean_interval',
                                                                'label': '清理间隔（分钟）',
                                                                'placeholder': '60',
                                                                'variant': 'outlined',
                                                                'type': 'number',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            
                            # 上传配置
                            {
                                'component': 'VExpansionPanel',
                                'props': {
                                    'value': 'upload'
                                },
                                'content': [
                                    {
                                        'component': 'VExpansionPanelTitle',
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'd-flex align-center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VIcon',
                                                        'props': {
                                                            'icon': 'mdi-upload',
                                                            'class': 'me-3',
                                                            'color': 'success'
                                                        }
                                                    },
                                                    {
                                                        'component': 'span',
                                                        'props': {
                                                            'class': 'text-h6'
                                                        },
                                                        'text': '上传配置'
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VExpansionPanelText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 3},
                                                        'content': [{
                                                            'component': 'VTextField',
                                                            'props': {
                                                                'model': 'upload_timeout',
                                                                'label': '上传超时（分钟）',
                                                                'placeholder': '60',
                                                                'variant': 'outlined',
                                                                'type': 'number',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 3},
                                                        'content': [{
                                                            'component': 'VTextField',
                                                            'props': {
                                                                'model': 'upload_retry_count',
                                                                'label': '重试次数',
                                                                'placeholder': '3',
                                                                'variant': 'outlined',
                                                                'type': 'number',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 3},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'delete_source_after_upload',
                                                                'label': '删除源文件',
                                                                'color': 'error',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 3},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_cookie_check',
                                                                'label': 'Cookie检测',
                                                                'color': 'warning',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            
                            # 企业级功能
                            {
                                'component': 'VExpansionPanel',
                                'props': {
                                    'value': 'enterprise'
                                },
                                'content': [
                                    {
                                        'component': 'VExpansionPanelTitle',
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'd-flex align-center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VIcon',
                                                        'props': {
                                                            'icon': 'mdi-crown',
                                                            'class': 'me-3',
                                                            'color': 'warning'
                                                        }
                                                    },
                                                    {
                                                        'component': 'span',
                                                        'props': {
                                                            'class': 'text-h6'
                                                        },
                                                        'text': '企业级功能'
                                                    },
                                                    {
                                                        'component': 'VChip',
                                                        'props': {
                                                            'size': 'small',
                                                            'color': 'warning',
                                                            'variant': 'flat',
                                                            'class': 'ms-3'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'span',
                                                                'text': 'Enterprise'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VExpansionPanelText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_enterprise_logging',
                                                                'label': '企业级日志',
                                                                'color': 'primary',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_distributed_lock',
                                                                'label': '分布式锁',
                                                                'color': 'success',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_health_check',
                                                                'label': '健康检查',
                                                                'color': 'info',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_quota_management',
                                                                'label': '配额管理',
                                                                'color': 'warning',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_api_handler',
                                                                'label': 'REST API',
                                                                'color': 'error',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 4},
                                                        'content': [{
                                                            'component': 'VSwitch',
                                                            'props': {
                                                                'model': 'enable_webhook_manager',
                                                                'label': 'WebHook',
                                                                'color': 'deep-purple',
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            
                            # 高级配置
                            {
                                'component': 'VExpansionPanel',
                                'props': {
                                    'value': 'advanced'
                                },
                                'content': [
                                    {
                                        'component': 'VExpansionPanelTitle',
                                        'content': [
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'd-flex align-center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VIcon',
                                                        'props': {
                                                            'icon': 'mdi-cogs',
                                                            'class': 'me-3',
                                                            'color': 'deep-purple'
                                                        }
                                                    },
                                                    {
                                                        'component': 'span',
                                                        'props': {
                                                            'class': 'text-h6'
                                                        },
                                                        'text': '高级配置'
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'VExpansionPanelText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 6},
                                                        'content': [{
                                                            'component': 'VTextarea',
                                                            'props': {
                                                                'model': 'cd2_confs',
                                                                'label': 'CloudDrive2配置',
                                                                'placeholder': 'host: http://ip:19798\nusername: admin\npassword: passwd\nsavepath: /home/media\nuploadpath: CD2Upload\nrootpath: 115\n\n---\n#组2配置...',
                                                                'variant': 'outlined',
                                                                'rows': 6,
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12, 'md': 6},
                                                        'content': [{
                                                            'component': 'VTextarea',
                                                            'props': {
                                                                'model': 'black_dirs',
                                                                'label': '过滤目录',
                                                                'placeholder': '每行一个目录，支持正则表达式\n例如：\n.*\\.tmp$\n/temp/\n.*test.*',
                                                                'variant': 'outlined',
                                                                'rows': 6,
                                                                'hide-details': True
                                                            }
                                                        }]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VRow',
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {'cols': 12},
                                                        'content': [{
                                                            'component': 'VTextField',
                                                            'props': {
                                                                'model': 'cloud_media_sync',
                                                                'label': 'Cloud Media Sync插件ID',
                                                                'placeholder': '请在Cloud Media Sync插件中查看其Plugin ID',
                                                                'variant': 'outlined',
                                                                'hide-details': True,
                                                                'prepend-inner-icon': 'mdi-link'
                                                            }
                                                        }]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            'enable': self._enable,
            'cron': self._cron,
            'onlyonce': self._onlyonce,
            'cleanlink': self._cleanlink,
            'monitor_upload': self._monitor_upload,
            'notify_upload': self._notify_upload,
            'upload_retry_count': self._upload_retry_count,
            'cd2_confs': self._cd2_confs,
            'cloud_media_sync': self._cloud_media_sync,
            'monitor_interval': self._monitor_interval,
            'clean_interval': self._clean_interval,
            'enable_cookie_check': self._enable_cookie_check,
            'cookie_check_interval': self._cookie_check_interval,
            'black_dirs': self._black_dirs,
            'upload_timeout': self._upload_timeout,
            'delete_source_after_upload': self._delete_source_after_upload,
            'enable_favorite_notify': self._enable_favorite_notify,
            'softlink_prefix_path': self._softlink_prefix_path,
            'cd_mount_prefix_path': self._cd_mount_prefix_path,
            # 企业级配置项
            'enable_enterprise_logging': getattr(self, '_enable_enterprise_logging', True),
            'enable_distributed_lock': getattr(self, '_enable_distributed_lock', True),
            'enable_health_check': getattr(self, '_enable_health_check', True),
            'enable_quota_management': getattr(self, '_enable_quota_management', True),
            'enable_api_handler': getattr(self, '_enable_api_handler', True),
            'enable_webhook_manager': getattr(self, '_enable_webhook_manager', True)
        }

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def get_page(self) -> List[dict]:
        pass

    def stop_service(self):
        """
        退出插件
        """
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error("退出插件失败：%s" % str(e))
