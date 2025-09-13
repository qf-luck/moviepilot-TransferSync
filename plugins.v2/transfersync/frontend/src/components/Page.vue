<script setup lang="ts">
import { ref, onMounted } from 'vue'

// 自定义事件，用于通知主应用刷新数据
const emit = defineEmits(['action', 'switch', 'close'])

// 接收API对象
const props = defineProps({
  api: {
    type: Object,
    default: () => {}
  }
})

// 状态数据
const status = ref({
  enabled: false,
  sync_paths_count: 0,
  sync_strategy: '',
  sync_type: '',
  delay_minutes: 0
})

// 加载状态
const loading = ref(false)
const syncing = ref(false)

// 获取同步状态
async function getStatus() {
  loading.value = true
  try {
    const result = await props.api.get('sync_status')
    if (result && !result.error) {
      status.value = result
    }
  } catch (error) {
    console.error('获取状态失败:', error)
  } finally {
    loading.value = false
  }
}

// 手动同步
async function manualSync() {
  syncing.value = true
  try {
    const result = await props.api.post('manual_sync')
    if (result.success) {
      console.log('手动同步完成:', result.message)
      // 刷新状态
      await getStatus()
    } else {
      console.error('手动同步失败:', result.error)
    }
  } catch (error) {
    console.error('手动同步异常:', error)
  } finally {
    syncing.value = false
  }
}

// 增量同步
async function incrementalSync() {
  syncing.value = true
  try {
    const result = await props.api.post('incremental_sync')
    if (result.success) {
      console.log('增量同步完成:', result.message)
      // 刷新状态
      await getStatus()
    } else {
      console.error('增量同步失败:', result.error)
    }
  } catch (error) {
    console.error('增量同步异常:', error)
  } finally {
    syncing.value = false
  }
}

// 通知主应用刷新数据
function notifyRefresh() {
  getStatus()
  emit('action')
}

// 通知主应用切换到配置页面
function notifySwitch() {
  emit('switch')
}

// 通知主应用关闭当前页面
function notifyClose() {
  emit('close')
}

onMounted(() => {
  getStatus()
})
</script>

<template>
  <div class="plugin-page pa-4">
    <v-card>
      <v-card-title class="d-flex align-center">
        <v-icon class="mr-2">mdi-sync</v-icon>
        整理后同步插件
        <v-spacer />
        <v-chip
          :color="status.enabled ? 'success' : 'error'"
          size="small"
          variant="flat"
        >
          {{ status.enabled ? '已启用' : '已禁用' }}
        </v-chip>
      </v-card-title>

      <v-card-text>
        <!-- 状态监控 -->
        <v-row class="mb-4">
          <v-col cols="12" md="3">
            <v-card variant="outlined" class="h-100">
              <v-card-text class="text-center">
                <div class="text-h4 mb-2" :class="status.enabled ? 'text-success' : 'text-error'">
                  {{ status.enabled ? '已启用' : '已禁用' }}
                </div>
                <div class="text-subtitle-2 text-medium-emphasis">
                  插件状态
                </div>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="3">
            <v-card variant="outlined" class="h-100">
              <v-card-text class="text-center">
                <div class="text-h4 mb-2 text-primary">
                  {{ status.sync_paths_count || 0 }}
                </div>
                <div class="text-subtitle-2 text-medium-emphasis">
                  同步路径数量
                </div>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="3">
            <v-card variant="outlined" class="h-100">
              <v-card-text class="text-center">
                <div class="text-h4 mb-2 text-info">
                  {{ status.sync_strategy || '--' }}
                </div>
                <div class="text-subtitle-2 text-medium-emphasis">
                  当前同步策略
                </div>
              </v-card-text>
            </v-card>
          </v-col>

          <v-col cols="12" md="3">
            <v-card variant="outlined" class="h-100">
              <v-card-text class="text-center">
                <div class="text-h4 mb-2 text-warning">
                  {{ status.sync_type || '--' }}
                </div>
                <div class="text-subtitle-2 text-medium-emphasis">
                  同步类型
                </div>
              </v-card-text>
            </v-card>
          </v-col>
        </v-row>

        <!-- 功能描述 -->
        <v-alert
          type="info"
          variant="tonal"
          class="mb-4"
        >
          <v-alert-title>功能说明</v-alert-title>
          监听多种事件类型自动同步文件到指定位置，支持多种同步策略，默认启用增量同步，简化配置更易使用。
        </v-alert>

        <!-- 操作面板 -->
        <v-card variant="outlined">
          <v-card-title>
            <v-icon class="mr-2">mdi-play</v-icon>
            操作控制
          </v-card-title>
          <v-card-text>
            <v-row>
              <v-col cols="12" sm="6" md="3">
                <v-btn
                  color="primary"
                  variant="elevated"
                  block
                  prepend-icon="mdi-sync"
                  :loading="syncing"
                  @click="manualSync"
                >
                  手动同步
                </v-btn>
              </v-col>

              <v-col cols="12" sm="6" md="3">
                <v-btn
                  color="success"
                  variant="elevated"
                  block
                  prepend-icon="mdi-update"
                  :loading="syncing"
                  @click="incrementalSync"
                >
                  增量同步
                </v-btn>
              </v-col>

              <v-col cols="12" sm="6" md="3">
                <v-btn
                  color="info"
                  variant="outlined"
                  block
                  prepend-icon="mdi-information"
                  :loading="loading"
                  @click="notifyRefresh"
                >
                  刷新状态
                </v-btn>
              </v-col>

              <v-col cols="12" sm="6" md="3">
                <v-btn
                  color="secondary"
                  variant="outlined"
                  block
                  prepend-icon="mdi-cog"
                  @click="notifySwitch"
                >
                  配置插件
                </v-btn>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>

        <!-- 详细信息 -->
        <v-expansion-panels class="mt-4">
          <v-expansion-panel>
            <v-expansion-panel-title>
              <v-icon class="mr-2">mdi-information-outline</v-icon>
              详细配置信息
            </v-expansion-panel-title>
            <v-expansion-panel-text>
              <v-list>
                <v-list-item>
                  <v-list-item-title>同步策略</v-list-item-title>
                  <v-list-item-subtitle>{{ status.sync_strategy || '未设置' }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title>同步类型</v-list-item-title>
                  <v-list-item-subtitle>{{ status.sync_type || '未设置' }}</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title>延迟时间</v-list-item-title>
                  <v-list-item-subtitle>{{ status.delay_minutes || 0 }} 分钟</v-list-item-subtitle>
                </v-list-item>
                <v-list-item>
                  <v-list-item-title>配置路径数</v-list-item-title>
                  <v-list-item-subtitle>{{ status.sync_paths_count || 0 }} 个</v-list-item-subtitle>
                </v-list-item>
              </v-list>
            </v-expansion-panel-text>
          </v-expansion-panel>
        </v-expansion-panels>
      </v-card-text>

      <v-card-actions class="px-4 pb-4">
        <v-btn
          variant="outlined"
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
.plugin-page {
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