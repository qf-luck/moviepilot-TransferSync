# TransferSync 插件错误修复总结

## 🐛 错误描述
```
插件 TransferSync 调用方法 get_page 出错: 'TransferSync' object has no attribute '_sync_paths'
```

## 🔧 问题原因
在重构插件时，我们将同步路径从多路径列表 `_sync_paths` 改为单一源路径和目标路径 `_source_path` + `_target_path`，但忘记更新所有引用 `_sync_paths` 的方法。

## ✅ 修复内容

### 1. 修复 `get_page()` 方法
**原来**:
```python
'text': f'配置的同步路径数量: {len(self._sync_paths)}'
```

**修复后**:
```python
'text': f'配置的同步路径数量: {1 if self._source_path and self._target_path else 0}'
```

### 2. 修复 `manual_sync()` 方法
**原来**:
```python
if not self._sync_paths:
    return {"error": "未配置同步路径"}

for sync_config in self._sync_paths:
    if self._sync_ops:
        self._sync_ops.execute_sync(sync_config, sync_config['source'])
        sync_count += 1
```

**修复后**:
```python
if not self._source_path or not self._target_path:
    return {"error": "未配置源路径或目标路径"}

if self._sync_ops:
    sync_config = {"source": self._source_path, "target": self._target_path}
    self._sync_ops.execute_sync(sync_config, self._source_path)
```

### 3. 修复 `incremental_sync()` 方法
**原来**:
```python
if not self._sync_paths:
    return {"error": "未配置同步路径"}

for sync_config in self._sync_paths:
    if self._sync_ops:
        result = self._sync_ops.execute_sync(sync_config, sync_config['source'])
        sync_count += 1
        total_files += result.get('synced_files', 0)
```

**修复后**:
```python
if not self._source_path or not self._target_path:
    return {"error": "未配置源路径或目标路径"}

if self._sync_ops:
    sync_config = {"source": self._source_path, "target": self._target_path}
    result = self._sync_ops.execute_sync(sync_config, self._source_path)
    total_files = result.get('synced_files', 0)
```

### 4. 修复 `test_paths()` 方法
**原来**:
```python
if not self._sync_paths:
    return {"error": "未配置同步路径"}

for i, sync_config in enumerate(self._sync_paths):
    source = sync_config.get('source', '')
    target = sync_config.get('target', '')
```

**修复后**:
```python
if not self._source_path or not self._target_path:
    return {"error": "未配置源路径或目标路径"}

source = self._source_path
target = self._target_path
```

### 5. 修复兼容配置方法
**原来**:
```python
if sync_paths_str:
    self._sync_paths = self._parse_sync_paths_compatible(sync_paths_str)
else:
    root_path = config.get("sync_root_path", "")
    target_path = config.get("sync_target_path", "")
    if root_path and target_path:
        self._sync_paths = [{"source": root_path, "target": target_path}]
    else:
        self._sync_paths = []
```

**修复后**:
```python
if sync_paths_str:
    sync_paths = self._parse_sync_paths_compatible(sync_paths_str)
    if sync_paths:
        self._source_path = sync_paths[0].get("source", "")
        self._target_path = sync_paths[0].get("target", "")
    else:
        self._source_path = ""
        self._target_path = ""
else:
    self._source_path = config.get("sync_root_path", "")
    self._target_path = config.get("sync_target_path", "")
```

## 🎯 修复结果

1. ✅ **插件语法检查通过**
2. ✅ **所有 `_sync_paths` 引用已移除**
3. ✅ **保持向后兼容性**
4. ✅ **功能逻辑保持一致**

## 🚀 测试建议

1. **重启 MoviePilot 服务**
2. **进入插件管理页面**
3. **查看 TransferSync 插件详情页面**
4. **测试配置保存和加载**
5. **验证手动同步功能**

现在插件应该可以正常工作了！