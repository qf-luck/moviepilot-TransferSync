<script setup lang="ts">
import { ref, onMounted } from 'vue'

// 接收初始配置和API对象
const props = defineProps({
  initialConfig: {
    type: Object,
    default: () => ({})
  },
  api: {
    type: Object,
    default: () => {}
  }
})

// 配置数据
const config = ref({
  enabled: false,
  source_path: '',
  target_path: '',
  sync_type: 'incremental',
  execution_mode: 'immediate',
  delay_minutes: 5,
  enable_notifications: false,
  notification_channel: 'telegram',
  sync_strategy: 'copy',
  max_depth: -1,
  file_filters: '',
  exclude_patterns: '',
  max_workers: 4,
  trigger_events: ['transfer.complete'],
  ...props.initialConfig
})

// 自定义事件，用于保存配置
const emit = defineEmits(['save', 'close', 'switch'])

// 加载状态
const loading = ref(false)
const saving = ref(false)

// 通知渠道选项
const notificationChannels = [
  { title: 'Telegram', value: 'telegram' },
  { title: '微信', value: 'wechat' },
  { title: 'QQ', value: 'qq' },
  { title: '钉钉', value: 'dingtalk' },
  { title: 'Slack', value: 'slack' },
  { title: 'Discord', value: 'discord' },
  { title: 'Bark', value: 'bark' },
  { title: 'PushPlus', value: 'pushplus' },
  { title: 'Gotify', value: 'gotify' },
  { title: 'Pushover', value: 'pushover' },
  { title: 'Server酱', value: 'serverchan' },
  { title: 'WebHook', value: 'webhook' }
]

// 同步策略选项
const syncStrategies = [
  { title: '复制文件', value: 'copy' },
  { title: '移动文件', value: 'move' },
  { title: '软链接', value: 'softlink' },
  { title: '硬链接', value: 'hardlink' }
]

// 同步类型选项
const syncTypes = [
  { title: '增量同步', value: 'incremental' },
  { title: '全量同步', value: 'full' }
]

// 执行模式选项
const executionModes = [
  { title: '立即执行', value: 'immediate' },
  { title: '延迟执行', value: 'delayed' }
]

// 触发事件选项
const triggerEventOptions = [
  { title: '整理完成', value: 'transfer.complete' },
  { title: '下载添加', value: 'download.added' },
  { title: '订阅完成', value: 'subscribe.complete' },
  { title: '媒体添加', value: 'media.added' },
  { title: '文件移动', value: 'file.moved' },
  { title: '目录扫描完成', value: 'directory.scan.complete' },
  { title: '刮削完成', value: 'scrape.complete' },
  { title: '插件触发', value: 'plugin.triggered' }
]

// 保存配置
async function saveConfig() {
  saving.value = true
  try {
    // 调用后端API保存配置
    const result = await props.api.post('save_config_api', config.value)
    if (result.success) {
      // 通知主应用保存成功
      emit('save', config.value)
    } else {
      console.error('保存配置失败:', result.error)
    }
  } catch (error) {
    console.error('保存配置异常:', error)
  } finally {
    saving.value = false
  }
}

// 测试配置
async function testConfig() {
  loading.value = true
  try {
    const result = await props.api.get('test_paths')
    if (result.success) {
      console.log('路径测试成功:', result.results)
    } else {
      console.error('路径测试失败:', result.error)
    }
  } catch (error) {
    console.error('路径测试异常:', error)
  } finally {
    loading.value = false
  }
}

// 通知主应用切换到详情页面
function notifySwitch() {
  emit('switch')
}

// 通知主应用关闭当前页面
function notifyClose() {
  emit('close')
}

// 加载配置
async function loadConfig() {
  loading.value = true
  try {
    const result = await props.api.get('get_config_api')
    if (result.success && result.data) {
      Object.assign(config.value, result.data)
    }
  } catch (error) {
    console.error('加载配置失败:', error)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <div class="plugin-config pa-4">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon class="mr-2">mdi-sync</v-icon>
        整理后同步插件配置
      </v-card-title>

      <v-card-text>
        <v-alert
          type="info"
          variant="tonal"
          class="mb-4"
        >
          监听多种事件类型自动同步文件到指定位置，支持多种同步策略，默认启用增量同步
        </v-alert>

        <v-form>
          <!-- 基础开关 -->
          <v-row>
            <v-col cols="12" md="6">
              <v-switch
                v-model="config.enabled"
                label="启用插件"
                color="primary"
                hide-details
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-switch
                v-model="config.enable_notifications"
                label="启用通知"
                color="primary"
                hide-details
              />
            </v-col>
          </v-row>

          <!-- 通知渠道选择 -->
          <v-row v-if="config.enable_notifications">
            <v-col cols="12" md="6">
              <v-select
                v-model="config.notification_channel"
                :items="notificationChannels"
                label="通知渠道"
                prepend-inner-icon="mdi-bell"
                variant="outlined"
              />
            </v-col>
          </v-row>

          <!-- 路径配置 -->
          <v-row>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="config.source_path"
                label="源路径"
                placeholder="请输入源路径，例如：/downloads"
                prepend-inner-icon="mdi-folder-outline"
                variant="outlined"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="config.target_path"
                label="目标路径"
                placeholder="请输入目标路径，例如：/media"
                prepend-inner-icon="mdi-folder"
                variant="outlined"
              />
            </v-col>
          </v-row>

          <!-- 同步策略配置 -->
          <v-row>
            <v-col cols="12" md="3">
              <v-select
                v-model="config.sync_type"
                :items="syncTypes"
                label="同步类型"
                prepend-inner-icon="mdi-sync"
                variant="outlined"
              />
            </v-col>
            <v-col cols="12" md="3">
              <v-select
                v-model="config.execution_mode"
                :items="executionModes"
                label="执行模式"
                prepend-inner-icon="mdi-clock-outline"
                variant="outlined"
              />
            </v-col>
            <v-col cols="12" md="3">
              <v-text-field
                v-model.number="config.delay_minutes"
                label="延迟时间（分钟）"
                type="number"
                :disabled="config.execution_mode !== 'delayed'"
                prepend-inner-icon="mdi-timer"
                variant="outlined"
                min="1"
              />
            </v-col>
            <v-col cols="12" md="3">
              <v-select
                v-model="config.sync_strategy"
                :items="syncStrategies"
                label="文件操作"
                prepend-inner-icon="mdi-file-move"
                variant="outlined"
              />
            </v-col>
          </v-row>

          <!-- 触发事件配置 -->
          <v-row>
            <v-col cols="12">
              <v-select
                v-model="config.trigger_events"
                :items="triggerEventOptions"
                label="触发事件"
                multiple
                chips
                prepend-inner-icon="mdi-lightning-bolt"
                variant="outlined"
                hint="选择触发同步的事件类型"
                persistent-hint
              />
            </v-col>
          </v-row>

          <!-- 文件过滤设置 -->
          <v-row>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="config.file_filters"
                label="文件类型过滤"
                placeholder="mp4,mkv,avi,mov"
                prepend-inner-icon="mdi-filter"
                variant="outlined"
                hint="只同步指定类型的文件，用逗号分隔"
                persistent-hint
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model="config.exclude_patterns"
                label="排除模式"
                placeholder="temp,cache,@eaDir"
                prepend-inner-icon="mdi-filter-remove"
                variant="outlined"
                hint="排除包含这些字符的文件/目录"
                persistent-hint
              />
            </v-col>
          </v-row>

          <!-- 高级设置 -->
          <v-row>
            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="config.max_depth"
                label="最大目录深度"
                type="number"
                placeholder="-1表示无限制"
                prepend-inner-icon="mdi-file-tree"
                variant="outlined"
              />
            </v-col>
            <v-col cols="12" md="6">
              <v-text-field
                v-model.number="config.max_workers"
                label="并发线程数"
                type="number"
                prepend-inner-icon="mdi-memory"
                variant="outlined"
                min="1"
                max="16"
              />
            </v-col>
          </v-row>
        </v-form>
      </v-card-text>

      <v-card-actions class="px-4 pb-4">
        <v-btn
          color="primary"
          variant="elevated"
          prepend-icon="mdi-content-save"
          :loading="saving"
          @click="saveConfig"
        >
          保存配置
        </v-btn>

        <v-btn
          color="secondary"
          variant="outlined"
          prepend-icon="mdi-test-tube"
          :loading="loading"
          @click="testConfig"
        >
          测试配置
        </v-btn>

        <v-spacer />

        <v-btn
          variant="outlined"
          prepend-icon="mdi-view-dashboard"
          @click="notifySwitch"
        >
          切换到详情页面
        </v-btn>

        <v-btn
          variant="text"
          prepend-icon="mdi-close"
          @click="notifyClose"
        >
          关闭页面
        </v-btn>
      </v-card-actions>
    </v-card>
  </div>
</template>

<style scoped>
.plugin-config {
  min-height: 100vh;
  background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
}

:deep(.v-card) {
  background: rgba(255, 255, 255, 0.95);
  backdrop-filter: blur(10px);
  border-radius: 12px;
}

:deep(.v-card-title) {
  font-size: 1.5rem;
  font-weight: 500;
}
</style>