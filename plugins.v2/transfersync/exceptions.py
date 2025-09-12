"""
自定义异常类
"""


class SyncException(Exception):
    """同步操作异常基类"""
    pass


class SyncPermissionError(SyncException):
    """同步权限错误"""
    pass


class SyncSpaceError(SyncException):
    """磁盘空间不足错误"""
    pass


class SyncNetworkError(SyncException):
    """网络连接错误"""
    pass