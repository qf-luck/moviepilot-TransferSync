<script setup lang="ts">
import { ref, onMounted } from 'vue'

// 接收配置和刷新控制
const props = defineProps({
  config: {
    type: Object,
    default: () => ({})
  },
  allowRefresh: {
    type: Boolean,
    default: true
  },
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
  last_sync_time: null
})

// 加载状态
const loading = ref(false)

// 获取同步状态
async function getStatus() {
  if (!props.allowRefresh) return

  loading.value = true
  try {
    const result = await props.api?.get('sync_status')
    if (result && !result.error) {
      status.value = {
        ...result,
        last_sync_time: new Date().toLocaleString()
      }
    }
  } catch (error) {
    console.error('获取状态失败:', error)
  } finally {
    loading.value = false
  }
}

// 格式化同步策略显示
function formatStrategy(strategy: string): string {
  const strategyMap: Record<string, string> = {
    'copy': '复制',
    'move': '移动',
    'softlink': '软链接',
    'hardlink': '硬链接'
  }
  return strategyMap[strategy] || strategy
}

// 格式化同步类型显示
function formatSyncType(type: string): string {
  const typeMap: Record<string, string> = {
    'incremental': '增量',
    'full': '全量'
  }
  return typeMap[type] || type
}

onMounted(() => {
  getStatus()
  // 如果允许刷新，设置定时刷新
  if (props.allowRefresh) {
    setInterval(getStatus, 30000) // 30秒刷新一次
  }
})
</script>

<template>
  <div class="dashboard-widget">
    <v-card class="h-100">
      <v-card-title class="d-flex align-center">
        <v-icon class="mr-2">mdi-sync</v-icon>
        {{ config.title || '整理后同步' }}
        <v-spacer />
        <v-chip
          :color="status.enabled ? 'success' : 'error'"
          size="small"
          variant="flat"
        >
          {{ status.enabled ? '运行中' : '已停止' }}
        </v-chip>
      </v-card-title>

      <v-card-text class="pb-2">
        <v-row dense>
          <!-- 路径数量 -->
          <v-col cols="6">
            <div class="text-center">
              <div class="text-h5 text-primary font-weight-bold">
                {{ status.sync_paths_count || 0 }}
              </div>
              <div class="text-caption text-medium-emphasis">
                同步路径
              </div>
            </div>
          </v-col>

          <!-- 同步策略 -->
          <v-col cols="6">
            <div class="text-center">
              <div class="text-h6 text-info">
                {{ formatStrategy(status.sync_strategy) || '--' }}
              </div>
              <div class="text-caption text-medium-emphasis">
                同步策略
              </div>
            </div>
          </v-col>
        </v-row>

        <v-divider class="my-3" />

        <!-- 详细信息 -->
        <div class="text-body-2">
          <div class="d-flex justify-space-between align-center mb-1">
            <span class="text-medium-emphasis">同步类型:</span>
            <span class="font-weight-medium">{{ formatSyncType(status.sync_type) || '未设置' }}</span>
          </div>

          <div class="d-flex justify-space-between align-center mb-1">
            <span class="text-medium-emphasis">状态:</span>
            <v-chip
              :color="status.enabled ? 'success' : 'grey'"
              size="x-small"
              variant="flat"
            >
              {{ status.enabled ? '启用' : '禁用' }}
            </v-chip>
          </div>

          <div v-if="status.last_sync_time" class="d-flex justify-space-between align-center">
            <span class="text-medium-emphasis">更新时间:</span>
            <span class="text-caption">{{ status.last_sync_time }}</span>
          </div>
        </div>
      </v-card-text>

      <v-card-actions class="pt-0">
        <v-btn
          size="small"
          variant="text"
          prepend-icon="mdi-refresh"
          :loading="loading"
          @click="getStatus"
        >
          刷新
        </v-btn>
        <v-spacer />
        <v-icon
          :class="status.enabled ? 'text-success' : 'text-grey'"
          size="20"
        >
          {{ status.enabled ? 'mdi-check-circle' : 'mdi-pause-circle' }}
        </v-icon>
      </v-card-actions>
    </v-card>
  </div>
</template>

<style scoped>
.dashboard-widget {
  height: 100%;
}

:deep(.v-card) {
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

:deep(.v-card-title) {
  font-size: 1.1rem;
  font-weight: 500;
  padding-bottom: 8px;
}

:deep(.v-card-text) {
  padding: 12px 16px;
}

:deep(.v-card-actions) {
  padding: 8px 16px 12px;
}
</style>