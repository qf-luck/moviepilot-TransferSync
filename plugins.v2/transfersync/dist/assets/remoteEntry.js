// TransferSync Plugin - Module Federation Entry
// 严格按照联邦.md文档规范

const moduleMap = {};

// Config组件
moduleMap['./Config'] = () => import('./config_component.js').catch(() => ({
  default: {
    name: 'TransferSyncConfig',
    props: {
      initialConfig: {
        type: Object,
        default: () => ({})
      },
      api: {
        type: Object,
        default: () => {}
      }
    },
    emits: ['save', 'close', 'switch'],
    data() {
      return {
        config: {
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
          trigger_events: ['transfer.complete']
        },
        loading: false,
        saving: false,
        notificationChannels: [
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
        ],
        syncStrategies: [
          { title: '复制文件', value: 'copy' },
          { title: '移动文件', value: 'move' },
          { title: '软链接', value: 'softlink' },
          { title: '硬链接', value: 'hardlink' }
        ],
        syncTypes: [
          { title: '增量同步', value: 'incremental' },
          { title: '全量同步', value: 'full' }
        ],
        executionModes: [
          { title: '立即执行', value: 'immediate' },
          { title: '延迟执行', value: 'delayed' }
        ],
        triggerEventOptions: [
          { title: '整理完成', value: 'transfer.complete' },
          { title: '下载添加', value: 'download.added' },
          { title: '订阅完成', value: 'subscribe.complete' },
          { title: '媒体添加', value: 'media.added' },
          { title: '文件移动', value: 'file.moved' }
        ]
      }
    },
    watch: {
      initialConfig: {
        immediate: true,
        handler(newConfig) {
          if (newConfig) {
            Object.assign(this.config, newConfig);
          }
        }
      }
    },
    methods: {
      async saveConfig() {
        this.saving = true;
        try {
          const result = await this.api.post('save_config_api', this.config);
          if (result.success) {
            this.$emit('save', this.config);
          } else {
            console.error('保存配置失败:', result.error);
          }
        } catch (error) {
          console.error('保存配置异常:', error);
        } finally {
          this.saving = false;
        }
      },
      async loadConfig() {
        this.loading = true;
        try {
          const result = await this.api.get('get_config_api');
          if (result.success && result.data) {
            Object.assign(this.config, result.data);
          }
        } catch (error) {
          console.error('加载配置失败:', error);
        } finally {
          this.loading = false;
        }
      }
    },
    mounted() {
      this.loadConfig();
    },
    template: \`
      <v-container class="pa-6">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="mr-3">mdi-sync</v-icon>
            整理后同步插件配置
          </v-card-title>

          <v-card-text>
            <v-alert type="info" class="mb-4">
              监听多种事件类型自动同步文件到指定位置，支持多种同步策略
            </v-alert>

            <v-form>
              <!-- 基础设置 -->
              <v-row>
                <v-col cols="12" md="6">
                  <v-switch
                    v-model="config.enabled"
                    label="启用插件"
                    color="primary"
                  />
                </v-col>
                <v-col cols="12" md="6">
                  <v-switch
                    v-model="config.enable_notifications"
                    label="启用通知"
                    color="primary"
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
                    prepend-icon="mdi-bell"
                  />
                </v-col>
              </v-row>

              <!-- 路径配置 -->
              <v-row>
                <v-col cols="12" md="6">
                  <v-text-field
                    v-model="config.source_path"
                    label="源路径"
                    placeholder="/downloads"
                    prepend-icon="mdi-folder-outline"
                  />
                </v-col>
                <v-col cols="12" md="6">
                  <v-text-field
                    v-model="config.target_path"
                    label="目标路径"
                    placeholder="/media"
                    prepend-icon="mdi-folder"
                  />
                </v-col>
              </v-row>

              <!-- 同步策略 -->
              <v-row>
                <v-col cols="12" md="3">
                  <v-select
                    v-model="config.sync_type"
                    :items="syncTypes"
                    label="同步类型"
                    prepend-icon="mdi-sync"
                  />
                </v-col>
                <v-col cols="12" md="3">
                  <v-select
                    v-model="config.execution_mode"
                    :items="executionModes"
                    label="执行模式"
                    prepend-icon="mdi-clock-outline"
                  />
                </v-col>
                <v-col cols="12" md="3">
                  <v-text-field
                    v-model.number="config.delay_minutes"
                    label="延迟时间（分钟）"
                    type="number"
                    :disabled="config.execution_mode !== 'delayed'"
                    prepend-icon="mdi-timer"
                  />
                </v-col>
                <v-col cols="12" md="3">
                  <v-select
                    v-model="config.sync_strategy"
                    :items="syncStrategies"
                    label="文件操作"
                    prepend-icon="mdi-file-move"
                  />
                </v-col>
              </v-row>

              <!-- 触发事件 -->
              <v-row>
                <v-col cols="12">
                  <v-select
                    v-model="config.trigger_events"
                    :items="triggerEventOptions"
                    label="触发事件"
                    multiple
                    chips
                    prepend-icon="mdi-lightning-bolt"
                  />
                </v-col>
              </v-row>

              <!-- 文件过滤 -->
              <v-row>
                <v-col cols="12" md="6">
                  <v-text-field
                    v-model="config.file_filters"
                    label="文件类型过滤"
                    placeholder="mp4,mkv,avi,mov"
                    prepend-icon="mdi-filter"
                  />
                </v-col>
                <v-col cols="12" md="6">
                  <v-text-field
                    v-model="config.exclude_patterns"
                    label="排除模式"
                    placeholder="temp,cache,@eaDir"
                    prepend-icon="mdi-filter-remove"
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
                    placeholder="-1（无限制）"
                    prepend-icon="mdi-file-tree"
                  />
                </v-col>
                <v-col cols="12" md="6">
                  <v-text-field
                    v-model.number="config.max_workers"
                    label="并发线程数"
                    type="number"
                    prepend-icon="mdi-memory"
                  />
                </v-col>
              </v-row>
            </v-form>
          </v-card-text>

          <v-card-actions>
            <v-btn
              color="primary"
              :loading="saving"
              @click="saveConfig"
            >
              保存配置
            </v-btn>
            <v-spacer />
            <v-btn
              variant="outlined"
              @click="$emit('close')"
            >
              关闭
            </v-btn>
          </v-card-actions>
        </v-card>
      </v-container>
    \`
  }
}));

// Page组件
moduleMap['./Page'] = () => import('./page_component.js').catch(() => ({
  default: {
    name: 'TransferSyncPage',
    props: {
      api: {
        type: Object,
        default: () => {}
      }
    },
    emits: ['action', 'switch', 'close'],
    data() {
      return {
        status: {
          enabled: false,
          sync_paths_count: 0,
          sync_strategy: '',
          sync_type: ''
        },
        loading: false,
        syncing: false
      }
    },
    methods: {
      async getStatus() {
        this.loading = true;
        try {
          const result = await this.api.get('sync_status');
          if (result && !result.error) {
            this.status = result;
          }
        } catch (error) {
          console.error('获取状态失败:', error);
        } finally {
          this.loading = false;
        }
      },
      async manualSync() {
        this.syncing = true;
        try {
          const result = await this.api.post('manual_sync');
          if (result.success) {
            await this.getStatus();
          }
        } catch (error) {
          console.error('同步失败:', error);
        } finally {
          this.syncing = false;
        }
      }
    },
    mounted() {
      this.getStatus();
    },
    template: \`
      <v-container class="pa-6">
        <v-card>
          <v-card-title class="d-flex align-center">
            <v-icon class="mr-3">mdi-sync</v-icon>
            整理后同步插件
            <v-spacer />
            <v-chip :color="status.enabled ? 'success' : 'error'" small>
              {{ status.enabled ? '已启用' : '已禁用' }}
            </v-chip>
          </v-card-title>

          <v-card-text>
            <!-- 状态卡片 -->
            <v-row class="mb-4">
              <v-col cols="12" sm="6" md="3">
                <v-card variant="outlined">
                  <v-card-text class="text-center">
                    <div class="text-h4" :class="status.enabled ? 'text-success' : 'text-error'">
                      {{ status.enabled ? '启用' : '禁用' }}
                    </div>
                    <div class="text-caption">插件状态</div>
                  </v-card-text>
                </v-card>
              </v-col>
              <v-col cols="12" sm="6" md="3">
                <v-card variant="outlined">
                  <v-card-text class="text-center">
                    <div class="text-h4 text-primary">{{ status.sync_paths_count || 0 }}</div>
                    <div class="text-caption">同步路径</div>
                  </v-card-text>
                </v-card>
              </v-col>
            </v-row>

            <!-- 操作按钮 -->
            <v-row>
              <v-col cols="12" sm="6" md="4">
                <v-btn
                  color="primary"
                  block
                  :loading="syncing"
                  @click="manualSync"
                >
                  <v-icon left>mdi-sync</v-icon>
                  手动同步
                </v-btn>
              </v-col>
              <v-col cols="12" sm="6" md="4">
                <v-btn
                  color="secondary"
                  block
                  @click="$emit('switch')"
                >
                  <v-icon left>mdi-cog</v-icon>
                  配置插件
                </v-btn>
              </v-col>
            </v-row>
          </v-card-text>
        </v-card>
      </v-container>
    \`
  }
}));

// Dashboard组件
moduleMap['./Dashboard'] = () => import('./dashboard_component.js').catch(() => ({
  default: {
    name: 'TransferSyncDashboard',
    props: {
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
    },
    data() {
      return {
        status: {
          enabled: false,
          sync_paths_count: 0,
          sync_strategy: ''
        },
        loading: false
      }
    },
    methods: {
      async getStatus() {
        if (!this.allowRefresh || !this.api) return;

        this.loading = true;
        try {
          const result = await this.api.get('sync_status');
          if (result && !result.error) {
            this.status = result;
          }
        } catch (error) {
          console.error('获取状态失败:', error);
        } finally {
          this.loading = false;
        }
      }
    },
    mounted() {
      this.getStatus();
      if (this.allowRefresh) {
        setInterval(this.getStatus, 30000);
      }
    },
    template: \`
      <v-card>
        <v-card-title class="d-flex align-center">
          <v-icon class="mr-2">mdi-sync</v-icon>
          {{ config.title || '整理后同步' }}
          <v-spacer />
          <v-chip :color="status.enabled ? 'success' : 'error'" small>
            {{ status.enabled ? '运行' : '停止' }}
          </v-chip>
        </v-card-title>

        <v-card-text>
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
        </v-card-text>

        <v-card-actions>
          <v-btn size="small" variant="text" :loading="loading" @click="getStatus">
            刷新
          </v-btn>
        </v-card-actions>
      </v-card>
    \`
  }
}));

// 模块联邦标准接口
const get = async (module) => {
  if (moduleMap[module]) {
    const moduleFactory = await moduleMap[module]();
    return moduleFactory;
  }
  throw new Error('Module "' + module + '" does not exist in container.');
};

const init = (shareScope) => {
  return Promise.resolve();
};

// 导出标准接口
export { get, init };