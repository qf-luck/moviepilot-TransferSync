<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'

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
  last_sync_time: '',
  total_synced_files: 0
})

const loading = ref(false)
let refreshInterval: NodeJS.Timeout | null = null

// 获取状态
async function getStatus() {
  if (!props.allowRefresh || !props.api) return

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

// 组件挂载时初始化
onMounted(() => {
  getStatus()
  if (props.allowRefresh) {
    // 每30秒刷新一次状态
    refreshInterval = setInterval(getStatus, 30000)
  }
})

// 组件卸载时清理定时器
onUnmounted(() => {
  if (refreshInterval) {
    clearInterval(refreshInterval)
  }
})
</script>

<template>
  <v-card class="dashboard-widget">
    <v-card-title class="d-flex align-center">
      <v-icon class="mr-2" size="small">mdi-sync</v-icon>
      {{ config.title || '整理后同步' }}
      <v-spacer />
      <v-chip
        :color="status.enabled ? 'success' : 'error'"
        size="x-small"
        variant="flat"
      >
        {{ status.enabled ? '运行' : '停止' }}
      </v-chip>
    </v-card-title>

    <v-card-text class="pb-2">
      <v-row dense>
        <v-col cols="6">
          <div class="text-center">
            <div class="text-h6 text-primary">{{ status.sync_paths_count || 0 }}</div>
            <div class="text-caption">同步路径</div>
          </div>
        </v-col>
        <v-col cols="6">
          <div class="text-center">
            <div class="text-subtitle-2">{{ status.sync_strategy || '--' }}</div>
            <div class="text-caption">同步策略</div>
          </div>
        </v-col>
      </v-row>

      <v-row dense class="mt-2" v-if="status.total_synced_files > 0">
        <v-col cols="12">
          <div class="text-center">
            <div class="text-subtitle-2 text-info">{{ status.total_synced_files }}</div>
            <div class="text-caption">已同步文件</div>
          </div>
        </v-col>
      </v-row>

      <v-row dense class="mt-2" v-if="status.last_sync_time">
        <v-col cols="12">
          <div class="text-caption text-center text-medium-emphasis">
            上次同步：{{ status.last_sync_time }}
          </div>
        </v-col>
      </v-row>
    </v-card-text>

    <v-card-actions class="pt-0">
      <v-btn
        size="small"
        variant="text"
        :loading="loading"
        @click="getStatus"
      >
        刷新
      </v-btn>
    </v-card-actions>
  </v-card>
</template>

<style scoped>
.dashboard-widget {
  height: 100%;
}

:deep(.v-card-title) {
  padding-bottom: 8px;
  font-size: 1rem;
}

:deep(.v-card-text) {
  padding-top: 8px;
  padding-bottom: 8px;
}

:deep(.v-card-actions) {
  padding-top: 0;
  padding-bottom: 8px;
}
</style>