# TransferSync Vue前端开发指南

## 概述

TransferSync 插件现在支持两种前端模式：
1. **Vuetify模式**（当前默认）：使用JSON配置的传统方式
2. **Vue模块联邦模式**：使用现代Vue 3组件的高级方式

## 启用Vue模式

1. 修改 `__init__.py` 中的 `get_render_mode()` 方法：
```python
@staticmethod
def get_render_mode() -> Tuple[str, Optional[str]]:
    return "vue", "dist/assets"  # 启用Vue模式
```

2. 在插件目录创建 `dist/assets/` 目录并放置编译后的Vue文件

## Vue前端开发步骤

### 1. 项目初始化
```bash
npm create vite@latest transfersync-frontend -- --template vue-ts
cd transfersync-frontend
yarn install
```

### 2. 安装必要依赖
```bash
yarn add @originjs/vite-plugin-federation
yarn add vuetify @mdi/font
```

### 3. Vite配置 (vite.config.ts)
```typescript
import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import federation from '@originjs/vite-plugin-federation'

export default defineConfig({
  plugins: [
    vue(),
    federation({
      name: 'TransferSyncPlugin',
      filename: 'remoteEntry.js',
      exposes: {
        './Config': './src/components/Config.vue',
        './Page': './src/components/Page.vue',
        './Dashboard': './src/components/Dashboard.vue'
      },
      shared: ['vue', 'vuetify']
    })
  ],
  build: {
    target: 'esnext',
    minify: false,
    cssCodeSplit: false
  }
})
```

### 4. 必需的Vue组件

#### Config.vue (配置页面)
```vue
<template>
  <v-container>
    <v-card>
      <v-card-title>TransferSync 配置</v-card-title>
      <v-card-text>
        <!-- 源路径配置 -->
        <v-row>
          <v-col cols="5">
            <v-text-field
              v-model="config.source_path"
              label="源路径"
              append-inner-icon="mdi-folder-search"
              @click:append-inner="openFileBrowser('source')"
            />
          </v-col>
          <v-col cols="5">
            <v-text-field
              v-model="config.target_path"
              label="目标路径"
              append-inner-icon="mdi-folder-search"
              @click:append-inner="openFileBrowser('target')"
            />
          </v-col>
          <v-col cols="2">
            <v-btn color="primary" @click="addPath">添加路径</v-btn>
          </v-col>
        </v-row>

        <!-- 同步策略 -->
        <v-row>
          <v-col cols="3">
            <v-select
              v-model="config.sync_type"
              :items="syncTypes"
              label="同步类型"
            />
          </v-col>
          <v-col cols="3">
            <v-select
              v-model="config.execution_mode"
              :items="executionModes"
              label="执行模式"
            />
          </v-col>
          <v-col cols="3" v-show="config.execution_mode === 'delayed'">
            <v-text-field
              v-model="config.delay_minutes"
              type="number"
              label="延迟时间（分钟）"
            />
          </v-col>
          <v-col cols="3">
            <v-select
              v-model="config.sync_strategy"
              :items="syncStrategies"
              label="文件操作"
            />
          </v-col>
        </v-row>

        <!-- 路径列表 -->
        <v-textarea
          v-model="config.sync_paths"
          label="同步路径列表"
          rows="3"
          readonly
        />
      </v-card-text>

      <v-card-actions>
        <v-btn color="primary" @click="saveConfig">保存配置</v-btn>
        <v-btn @click="testPaths">测试路径</v-btn>
      </v-card-actions>
    </v-card>

    <!-- 文件浏览对话框 -->
    <v-dialog v-model="fileBrowserDialog" max-width="800">
      <v-card>
        <v-card-title>选择目录</v-card-title>
        <v-card-text>
          <v-text-field
            v-model="currentPath"
            label="当前路径"
            readonly
          />
          <v-list>
            <v-list-item
              v-for="item in fileList"
              :key="item.path"
              @click="selectFile(item)"
            >
              <v-list-item-title>
                <v-icon>{{ item.type === 'dir' ? 'mdi-folder' : 'mdi-file' }}</v-icon>
                {{ item.name }}
              </v-list-item-title>
            </v-list-item>
          </v-list>
        </v-card-text>
        <v-card-actions>
          <v-btn @click="fileBrowserDialog = false">取消</v-btn>
          <v-btn color="primary" @click="confirmPath">确定</v-btn>
        </v-card-actions>
      </v-card>
    </v-dialog>
  </v-container>
</template>

<script setup lang="ts">
import { ref, onMounted } from 'vue'

// Props
const props = defineProps<{
  api: any
}>()

// 配置数据
const config = ref({
  enabled: false,
  source_path: '',
  target_path: '',
  sync_paths: '',
  sync_type: 'incremental',
  execution_mode: 'immediate',
  delay_minutes: 5,
  sync_strategy: 'copy'
})

// 选项数据
const syncTypes = [
  { title: '增量同步', value: 'incremental' },
  { title: '全量同步', value: 'full' }
]

const executionModes = [
  { title: '立即执行', value: 'immediate' },
  { title: '延迟执行', value: 'delayed' }
]

const syncStrategies = [
  { title: '复制文件', value: 'copy' },
  { title: '移动文件', value: 'move' },
  { title: '软链接', value: 'softlink' },
  { title: '硬链接', value: 'hardlink' }
]

// 文件浏览器
const fileBrowserDialog = ref(false)
const currentPath = ref('')
const fileList = ref([])
const browseType = ref('')

// 方法
const loadConfig = async () => {
  try {
    const response = await props.api.get('get_config_api')
    if (response.success) {
      Object.assign(config.value, response.data)
    }
  } catch (error) {
    console.error('加载配置失败:', error)
  }
}

const saveConfig = async () => {
  try {
    const response = await props.api.post('save_config_api', config.value)
    if (response.success) {
      // 显示成功提示
    }
  } catch (error) {
    console.error('保存配置失败:', error)
  }
}

const openFileBrowser = async (type: string) => {
  browseType.value = type
  fileBrowserDialog.value = true
  await loadFileList('/')
}

const loadFileList = async (path: string) => {
  try {
    const response = await props.api.get('browse_files', { params: { path } })
    if (response.success) {
      currentPath.value = response.current_path
      fileList.value = response.items
    }
  } catch (error) {
    console.error('加载文件列表失败:', error)
  }
}

const selectFile = async (item: any) => {
  if (item.type === 'dir') {
    await loadFileList(item.path)
  }
}

const confirmPath = () => {
  if (browseType.value === 'source') {
    config.value.source_path = currentPath.value
  } else {
    config.value.target_path = currentPath.value
  }
  fileBrowserDialog.value = false
}

const addPath = () => {
  if (config.value.source_path && config.value.target_path) {
    const newPath = `${config.value.source_path}->${config.value.target_path}`
    if (config.value.sync_paths) {
      config.value.sync_paths += '\n' + newPath
    } else {
      config.value.sync_paths = newPath
    }
    config.value.source_path = ''
    config.value.target_path = ''
  }
}

const testPaths = async () => {
  try {
    const response = await props.api.get('test_paths')
    // 显示测试结果
  } catch (error) {
    console.error('测试路径失败:', error)
  }
}

// 初始化
onMounted(() => {
  loadConfig()
})
</script>
```

### 5. 构建和部署

```bash
# 构建
yarn build

# 将 dist/assets 目录复制到插件的 dist/assets 目录
cp -r dist/assets/* ../transfersync/dist/assets/
```

## API接口

TransferSync为Vue前端提供了以下API：

- `GET /get_config_api` - 获取配置数据
- `POST /save_config_api` - 保存配置
- `GET /browse_files?path=xxx` - 浏览文件目录
- `GET /test_paths` - 测试路径
- `POST /manual_sync` - 手动同步

所有API都使用"bear"认证，需要在请求头中包含认证信息。

## 文件结构

```
transfersync/
├── dist/
│   └── assets/           # Vue编译后的文件
│       ├── *.js
│       ├── *.css
│       └── remoteEntry.js
├── __init__.py          # 主插件文件
└── VUE_FRONTEND_GUIDE.md # 本指南
```

## 注意事项

1. Vue模式和Vuetify模式可以随时切换
2. 文件浏览器API已经准备好，支持目录导航
3. 所有配置数据通过API传递，支持实时更新
4. 遵循MoviePilot的模块联邦规范