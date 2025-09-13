"""
健康检查模块
"""
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Any, List
from app.log import logger


class HealthChecker:
    """健康检查器"""
    
    def __init__(self, plugin_instance):
        self.plugin = plugin_instance
        self.last_check = None
        self.check_history = []
        self.max_history = 50  # 保留最近50次检查记录
        
    def perform_health_check(self) -> Dict[str, Any]:
        """执行完整的健康检查"""
        check_start = datetime.now()
        health_status = {
            'timestamp': check_start.isoformat(),
            'overall_status': 'healthy',
            'checks': {},
            'warnings': [],
            'errors': [],
            'performance': {}
        }
        
        try:
            # 路径检查
            path_check = self._check_paths()
            health_status['checks']['paths'] = path_check
            if not path_check['status']:
                health_status['overall_status'] = 'unhealthy'
                health_status['errors'].extend(path_check.get('errors', []))
            
            # 权限检查
            permission_check = self._check_permissions()
            health_status['checks']['permissions'] = permission_check
            if not permission_check['status']:
                health_status['overall_status'] = 'unhealthy'
                health_status['errors'].extend(permission_check.get('errors', []))
            
            # 磁盘空间检查
            space_check = self._check_disk_space()
            health_status['checks']['disk_space'] = space_check
            if not space_check['status']:
                health_status['overall_status'] = 'degraded'
                health_status['warnings'].extend(space_check.get('warnings', []))
            
            # 配置验证
            config_check = self._check_configuration()
            health_status['checks']['configuration'] = config_check
            if not config_check['status']:
                health_status['overall_status'] = 'unhealthy'
                health_status['errors'].extend(config_check.get('errors', []))
            
            # 性能指标检查
            performance_check = self._check_performance()
            health_status['checks']['performance'] = performance_check
            health_status['performance'] = performance_check.get('metrics', {})
            
            # 同步状态检查
            sync_check = self._check_sync_status()
            health_status['checks']['sync_status'] = sync_check
            if sync_check.get('warnings'):
                health_status['warnings'].extend(sync_check['warnings'])
            
        except Exception as e:
            health_status['overall_status'] = 'error'
            health_status['errors'].append(f"健康检查异常: {str(e)}")
            logger.error(f"健康检查异常: {str(e)}")
        
        # 记录检查耗时
        check_duration = (datetime.now() - check_start).total_seconds()
        health_status['check_duration'] = check_duration
        
        # 更新检查历史
        self._update_check_history(health_status)
        self.last_check = check_start
        
        # 更新插件健康状态
        self.plugin._health_status = health_status
        
        return health_status
    
    def _check_paths(self) -> Dict[str, Any]:
        """检查路径可用性"""
        result = {
            'status': True,
            'details': {},
            'errors': []
        }
        
        try:
            # 检查根路径
            if self.plugin._sync_root_path:
                root_path = Path(self.plugin._sync_root_path)
                if not root_path.exists():
                    result['status'] = False
                    result['errors'].append(f"根路径不存在: {self.plugin._sync_root_path}")
                elif not root_path.is_dir():
                    result['status'] = False
                    result['errors'].append(f"根路径不是目录: {self.plugin._sync_root_path}")
                else:
                    result['details']['root_path'] = {
                        'path': str(root_path),
                        'exists': True,
                        'readable': os.access(root_path, os.R_OK)
                    }
            else:
                result['status'] = False
                result['errors'].append("未配置根路径")
            
            # 检查目标路径
            if self.plugin._sync_target_path:
                target_path = Path(self.plugin._sync_target_path)
                if not target_path.exists():
                    # 尝试创建目标路径
                    try:
                        target_path.mkdir(parents=True, exist_ok=True)
                        result['details']['target_path_created'] = True
                    except Exception as e:
                        result['status'] = False
                        result['errors'].append(f"无法创建目标路径: {str(e)}")
                
                if target_path.exists():
                    result['details']['target_path'] = {
                        'path': str(target_path),
                        'exists': True,
                        'writable': os.access(target_path, os.W_OK)
                    }
                else:
                    result['status'] = False
                    result['errors'].append(f"目标路径不存在且无法创建: {self.plugin._sync_target_path}")
            else:
                result['status'] = False
                result['errors'].append("未配置目标路径")
                
        except Exception as e:
            result['status'] = False
            result['errors'].append(f"路径检查异常: {str(e)}")
            
        return result
    
    def _check_permissions(self) -> Dict[str, Any]:
        """检查文件权限"""
        result = {
            'status': True,
            'details': {},
            'errors': []
        }
        
        try:
            # 检查根路径权限
            if self.plugin._sync_root_path:
                root_path = Path(self.plugin._sync_root_path)
                if root_path.exists():
                    readable = os.access(root_path, os.R_OK)
                    executable = os.access(root_path, os.X_OK)
                    
                    result['details']['root_permissions'] = {
                        'readable': readable,
                        'executable': executable
                    }
                    
                    if not readable or not executable:
                        result['status'] = False
                        result['errors'].append(f"根路径权限不足: {self.plugin._sync_root_path}")
            
            # 检查目标路径权限
            if self.plugin._sync_target_path:
                target_path = Path(self.plugin._sync_target_path)
                if target_path.exists():
                    readable = os.access(target_path, os.R_OK)
                    writable = os.access(target_path, os.W_OK)
                    executable = os.access(target_path, os.X_OK)
                    
                    result['details']['target_permissions'] = {
                        'readable': readable,
                        'writable': writable,
                        'executable': executable
                    }
                    
                    if not writable or not executable:
                        result['status'] = False
                        result['errors'].append(f"目标路径权限不足: {self.plugin._sync_target_path}")
                        
        except Exception as e:
            result['status'] = False
            result['errors'].append(f"权限检查异常: {str(e)}")
            
        return result
    
    def _check_disk_space(self) -> Dict[str, Any]:
        """检查磁盘空间"""
        result = {
            'status': True,
            'details': {},
            'warnings': []
        }
        
        try:
            # 检查目标路径磁盘空间
            if self.plugin._sync_target_path:
                target_path = Path(self.plugin._sync_target_path)
                if target_path.exists():
                    stat = os.statvfs(target_path)
                    
                    # 计算空间信息 (字节)
                    total_space = stat.f_blocks * stat.f_frsize
                    free_space = stat.f_available * stat.f_frsize
                    used_space = total_space - free_space
                    usage_percent = (used_space / total_space) * 100 if total_space > 0 else 0
                    
                    result['details']['disk_usage'] = {
                        'total_gb': round(total_space / (1024**3), 2),
                        'free_gb': round(free_space / (1024**3), 2),
                        'used_gb': round(used_space / (1024**3), 2),
                        'usage_percent': round(usage_percent, 2)
                    }
                    
                    # 警告阈值检查
                    if usage_percent > 90:
                        result['status'] = False
                        result['warnings'].append(f"目标路径磁盘使用率过高: {usage_percent:.1f}%")
                    elif usage_percent > 80:
                        result['warnings'].append(f"目标路径磁盘使用率较高: {usage_percent:.1f}%")
                    
                    # 剩余空间检查 (小于1GB)
                    if free_space < 1024**3:
                        result['status'] = False
                        result['warnings'].append(f"目标路径剩余空间不足: {free_space / (1024**3):.1f}GB")
                        
        except Exception as e:
            result['warnings'].append(f"磁盘空间检查异常: {str(e)}")
            
        return result
    
    def _check_configuration(self) -> Dict[str, Any]:
        """检查配置有效性"""
        result = {
            'status': True,
            'details': {},
            'errors': []
        }
        
        try:
            # 基础配置检查
            if not self.plugin._enabled:
                result['details']['plugin_enabled'] = False
                result['errors'].append("插件未启用")
                result['status'] = False
            else:
                result['details']['plugin_enabled'] = True
            
            # 路径配置检查
            if not self.plugin._sync_root_path or not self.plugin._sync_target_path:
                result['status'] = False
                result['errors'].append("路径配置不完整")
            
            # 同步策略检查
            valid_strategies = ['copy', 'move', 'hardlink', 'softlink']
            if self.plugin._sync_strategy.value not in valid_strategies:
                result['status'] = False
                result['errors'].append(f"无效的同步策略: {self.plugin._sync_strategy.value}")
            
            # 事件监听检查
            if not self.plugin._trigger_events:
                result['status'] = False
                result['errors'].append("未配置触发事件")
            
            result['details']['configuration'] = {
                'sync_strategy': self.plugin._sync_strategy.value,
                'sync_mode': self.plugin._sync_mode.value,
                'trigger_events': [event.value for event in self.plugin._trigger_events],
                'notifications_enabled': self.plugin._enable_notifications
            }
            
        except Exception as e:
            result['status'] = False
            result['errors'].append(f"配置检查异常: {str(e)}")
            
        return result
    
    def _check_performance(self) -> Dict[str, Any]:
        """检查性能指标"""
        result = {
            'status': True,
            'metrics': {},
            'warnings': []
        }
        
        try:
            # 事件处理性能
            event_stats = self.plugin._event_statistics
            if event_stats:
                total_events = sum(stats.get('total_count', 0) for stats in event_stats.values())
                total_success = sum(stats.get('success_count', 0) for stats in event_stats.values())
                avg_processing_time = sum(stats.get('avg_processing_time', 0) for stats in event_stats.values()) / len(event_stats) if event_stats else 0
                
                success_rate = (total_success / total_events * 100) if total_events > 0 else 100
                
                result['metrics']['event_processing'] = {
                    'total_events': total_events,
                    'success_rate': round(success_rate, 2),
                    'avg_processing_time': round(avg_processing_time, 3)
                }
                
                # 性能警告
                if success_rate < 80:
                    result['warnings'].append(f"事件处理成功率较低: {success_rate:.1f}%")
                if avg_processing_time > 5:
                    result['warnings'].append(f"事件处理平均耗时过长: {avg_processing_time:.1f}秒")
            
            # 同步性能
            if hasattr(self.plugin, '_sync_ops') and self.plugin._sync_ops:
                sync_records = self.plugin._sync_ops.sync_records
                if sync_records:
                    recent_syncs = [
                        record for record in sync_records.values()
                        if record['sync_time'] > datetime.now() - timedelta(hours=24)
                    ]
                    
                    result['metrics']['sync_performance'] = {
                        'recent_syncs_24h': len(recent_syncs),
                        'total_syncs': len(sync_records)
                    }
            
        except Exception as e:
            result['warnings'].append(f"性能检查异常: {str(e)}")
            
        return result
    
    def _check_sync_status(self) -> Dict[str, Any]:
        """检查同步状态"""
        result = {
            'status': True,
            'details': {},
            'warnings': []
        }
        
        try:
            # 最近同步状态
            if self.plugin._last_sync_time:
                last_sync_age = datetime.now() - self.plugin._last_sync_time
                result['details']['last_sync'] = {
                    'time': self.plugin._last_sync_time.isoformat(),
                    'hours_ago': round(last_sync_age.total_seconds() / 3600, 1)
                }
                
                # 长时间未同步警告
                if last_sync_age > timedelta(days=7):
                    result['warnings'].append(f"距离上次同步已超过7天: {last_sync_age.days}天前")
            else:
                result['details']['last_sync'] = None
                result['warnings'].append("从未执行过同步")
            
            # 调度器状态
            if hasattr(self.plugin, 'sync_scheduler'):
                try:
                    job_status = self.plugin.sync_scheduler.get_job_status()
                    result['details']['scheduler'] = job_status
                    
                    if job_status.get('status') == 'error':
                        result['warnings'].append("调度器状态异常")
                except Exception:
                    result['warnings'].append("无法获取调度器状态")
            
        except Exception as e:
            result['warnings'].append(f"同步状态检查异常: {str(e)}")
            
        return result
    
    def _update_check_history(self, health_status: Dict[str, Any]):
        """更新检查历史"""
        try:
            # 添加新的检查记录
            self.check_history.append({
                'timestamp': health_status['timestamp'],
                'overall_status': health_status['overall_status'],
                'error_count': len(health_status.get('errors', [])),
                'warning_count': len(health_status.get('warnings', [])),
                'check_duration': health_status.get('check_duration', 0)
            })
            
            # 保持历史记录数量限制
            if len(self.check_history) > self.max_history:
                self.check_history = self.check_history[-self.max_history:]
                
        except Exception as e:
            logger.error(f"更新健康检查历史失败: {str(e)}")
    
    def get_health_summary(self) -> Dict[str, Any]:
        """获取健康状态摘要"""
        if not self.check_history:
            return {'status': 'no_data', 'message': '暂无健康检查数据'}
        
        latest_check = self.check_history[-1]
        recent_checks = self.check_history[-10:]  # 最近10次检查
        
        # 计算统计信息
        healthy_count = sum(1 for check in recent_checks if check['overall_status'] == 'healthy')
        health_rate = (healthy_count / len(recent_checks)) * 100
        
        avg_check_duration = sum(check['check_duration'] for check in recent_checks) / len(recent_checks)
        
        return {
            'current_status': latest_check['overall_status'],
            'last_check': latest_check['timestamp'],
            'health_rate': round(health_rate, 1),
            'avg_check_duration': round(avg_check_duration, 3),
            'recent_errors': sum(check['error_count'] for check in recent_checks),
            'recent_warnings': sum(check['warning_count'] for check in recent_checks)
        }