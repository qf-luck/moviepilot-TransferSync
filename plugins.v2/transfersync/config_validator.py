"""
配置验证器
"""
import os
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List
from apscheduler.triggers.cron import CronTrigger

from .sync_types import TriggerEvent


class ConfigValidator:
    """高级配置验证器"""
    
    def __init__(self):
        self.errors = []
        self.warnings = []
        
    def validate_paths(self, paths: List[str]) -> bool:
        """验证路径配置"""
        valid = True
        
        if not paths:
            self.errors.append("未配置同步目标路径")
            return False
            
        for path_str in paths:
            try:
                path = Path(path_str)
                
                # 检查路径是否存在
                if not path.exists():
                    try:
                        # 尝试创建路径
                        path.mkdir(parents=True, exist_ok=True)
                        self.warnings.append(f"自动创建目标路径: {path}")
                    except Exception as e:
                        self.errors.append(f"无法创建目标路径 {path}: {str(e)}")
                        valid = False
                        continue
                
                # 检查是否为目录
                if not path.is_dir():
                    self.errors.append(f"路径不是目录: {path}")
                    valid = False
                    continue
                    
                # 检查写权限
                if not os.access(path, os.W_OK):
                    self.errors.append(f"路径无写权限: {path}")
                    valid = False
                    continue
                    
                # 检查磁盘空间
                try:
                    free_space = shutil.disk_usage(path).free
                    if free_space < 1024 * 1024 * 100:  # 小于100MB
                        self.warnings.append(f"路径可用空间较少 ({free_space / 1024 / 1024:.1f}MB): {path}")
                except Exception:
                    self.warnings.append(f"无法获取磁盘空间信息: {path}")
                    
            except Exception as e:
                self.errors.append(f"路径验证失败 {path_str}: {str(e)}")
                valid = False
        
        return valid
    
    def validate_cron_expressions(self, expressions: Dict[str, str]) -> bool:
        """验证Cron表达式"""
        valid = True
        
        for name, expression in expressions.items():
            if not expression:
                continue
                
            try:
                CronTrigger.from_crontab(expression)
            except Exception as e:
                self.errors.append(f"{name}Cron表达式无效 '{expression}': {str(e)}")
                valid = False
                
        return valid
    
    def validate_numeric_ranges(self, configs: Dict[str, tuple]) -> bool:
        """验证数值范围配置"""
        valid = True
        
        for name, (value, min_val, max_val) in configs.items():
            if value < min_val:
                self.errors.append(f"{name}不能小于{min_val}，当前值: {value}")
                valid = False
            elif max_val is not None and value > max_val:
                self.errors.append(f"{name}不能大于{max_val}，当前值: {value}")
                valid = False
                
        return valid
    
    def validate_regex_patterns(self, patterns: List[str]) -> bool:
        """验证正则表达式模式"""
        valid = True
        
        for pattern in patterns:
            if not pattern:
                continue
                
            try:
                re.compile(pattern)
            except re.error as e:
                self.errors.append(f"正则表达式无效 '{pattern}': {str(e)}")
                valid = False
                
        return valid
    
    def validate_file_extensions(self, extensions: List[str]) -> bool:
        """验证文件扩展名配置"""
        valid = True
        
        for ext in extensions:
            if not ext:
                continue
                
            # 确保扩展名以.开头
            if not ext.startswith('.'):
                self.warnings.append(f"文件扩展名建议以'.'开头: {ext}")
            
            # 检查是否包含无效字符
            invalid_chars = ['/', '\\', ':', '*', '?', '"', '<', '>', '|']
            if any(char in ext for char in invalid_chars):
                self.errors.append(f"文件扩展名包含无效字符: {ext}")
                valid = False
                
        return valid
    
    def validate_trigger_events(self, events: List[str]) -> bool:
        """验证触发事件配置"""
        valid = True
        
        if not events:
            self.warnings.append("未选择任何触发事件，插件将不会自动触发")
            return True
            
        valid_events = [e.value for e in TriggerEvent]
        for event in events:
            if event not in valid_events:
                self.errors.append(f"无效的触发事件类型: {event}")
                valid = False
                
        return valid
    
    def get_summary(self) -> Dict[str, Any]:
        """获取验证摘要"""
        return {
            'valid': len(self.errors) == 0,
            'error_count': len(self.errors),
            'warning_count': len(self.warnings),
            'errors': self.errors,
            'warnings': self.warnings
        }
    
    def has_errors(self) -> bool:
        """是否有错误"""
        return len(self.errors) > 0
    
    def has_warnings(self) -> bool:
        """是否有警告"""
        return len(self.warnings) > 0
    
    def clear(self):
        """清除验证结果"""
        self.errors.clear()
        self.warnings.clear()