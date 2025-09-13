const __federation_var___federation_fn_import__ = (n) => {
  return import(n);
};

let Config, Page, Dashboard;

const __federation_expose_Config__ = {
  get: () => Config,
  init: async () => {
    Config = {
      __esModule: true,
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
              { title: '文件移动', value: 'file.moved' },
              { title: '目录扫描完成', value: 'directory.scan.complete' },
              { title: '刮削完成', value: 'scrape.complete' },
              { title: '插件触发', value: 'plugin.triggered' }
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
          async testConfig() {
            this.loading = true;
            try {
              const result = await this.api.get('test_paths');
              if (result.success) {
                console.log('路径测试成功:', result.results);
              } else {
                console.error('路径测试失败:', result.error);
              }
            } catch (error) {
              console.error('路径测试异常:', error);
            } finally {
              this.loading = false;
            }
          },
          notifySwitch() {
            this.$emit('switch');
          },
          notifyClose() {
            this.$emit('close');
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
          <div class="plugin-config pa-4" style="min-height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
            <v-card style="background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-radius: 12px;">
              <v-card-title class="d-flex align-center">
                <v-icon class="mr-2">mdi-sync</v-icon>
                整理后同步插件配置
              </v-card-title>

              <v-card-text>
                <v-alert type="info" variant="tonal" class="mb-4">
                  监听多种事件类型自动同步文件到指定位置，支持多种同步策略，默认启用增量同步
                </v-alert>

                <v-form>
                  <v-row>
                    <v-col cols="12" md="6">
                      <v-switch v-model="config.enabled" label="启用插件" color="primary" hide-details />
                    </v-col>
                    <v-col cols="12" md="6">
                      <v-switch v-model="config.enable_notifications" label="启用通知" color="primary" hide-details />
                    </v-col>
                  </v-row>

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
                <v-btn color="primary" variant="elevated" prepend-icon="mdi-content-save" :loading="saving" @click="saveConfig">
                  保存配置
                </v-btn>
                <v-btn color="secondary" variant="outlined" prepend-icon="mdi-test-tube" :loading="loading" @click="testConfig">
                  测试配置
                </v-btn>
                <v-spacer />
                <v-btn variant="outlined" prepend-icon="mdi-view-dashboard" @click="notifySwitch">
                  切换到详情页面
                </v-btn>
                <v-btn variant="text" prepend-icon="mdi-close" @click="notifyClose">
                  关闭页面
                </v-btn>
              </v-card-actions>
            </v-card>
          </div>
        \`
      }
    };
  }
};

const __federation_expose_Page__ = {
  get: () => Page,
  init: async () => {
    Page = {
      __esModule: true,
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
              sync_type: '',
              delay_minutes: 0
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
                console.log('手动同步完成:', result.message);
                await this.getStatus();
              } else {
                console.error('手动同步失败:', result.error);
              }
            } catch (error) {
              console.error('手动同步异常:', error);
            } finally {
              this.syncing = false;
            }
          },
          async incrementalSync() {
            this.syncing = true;
            try {
              const result = await this.api.post('incremental_sync');
              if (result.success) {
                console.log('增量同步完成:', result.message);
                await this.getStatus();
              } else {
                console.error('增量同步失败:', result.error);
              }
            } catch (error) {
              console.error('增量同步异常:', error);
            } finally {
              this.syncing = false;
            }
          },
          notifyRefresh() {
            this.getStatus();
            this.$emit('action');
          },
          notifySwitch() {
            this.$emit('switch');
          },
          notifyClose() {
            this.$emit('close');
          }
        },
        mounted() {
          this.getStatus();
        },
        template: \`
          <div class="plugin-page pa-4" style="min-height: 100vh; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);">
            <v-card style="background: rgba(255, 255, 255, 0.95); backdrop-filter: blur(10px); border-radius: 12px;">
              <v-card-title class="d-flex align-center">
                <v-icon class="mr-2">mdi-sync</v-icon>
                整理后同步插件
                <v-spacer />
                <v-chip :color="status.enabled ? 'success' : 'error'" size="small" variant="flat">
                  {{ status.enabled ? '已启用' : '已禁用' }}
                </v-chip>
              </v-card-title>

              <v-card-text>
                <v-row class="mb-4">
                  <v-col cols="12" md="3">
                    <v-card variant="outlined" class="h-100">
                      <v-card-text class="text-center">
                        <div class="text-h4 mb-2" :class="status.enabled ? 'text-success' : 'text-error'">
                          {{ status.enabled ? '已启用' : '已禁用' }}
                        </div>
                        <div class="text-subtitle-2 text-medium-emphasis">插件状态</div>
                      </v-card-text>
                    </v-card>
                  </v-col>
                  <v-col cols="12" md="3">
                    <v-card variant="outlined" class="h-100">
                      <v-card-text class="text-center">
                        <div class="text-h4 mb-2 text-primary">{{ status.sync_paths_count || 0 }}</div>
                        <div class="text-subtitle-2 text-medium-emphasis">同步路径数量</div>
                      </v-card-text>
                    </v-card>
                  </v-col>
                  <v-col cols="12" md="3">
                    <v-card variant="outlined" class="h-100">
                      <v-card-text class="text-center">
                        <div class="text-h4 mb-2 text-info">{{ status.sync_strategy || '--' }}</div>
                        <div class="text-subtitle-2 text-medium-emphasis">当前同步策略</div>
                      </v-card-text>
                    </v-card>
                  </v-col>
                  <v-col cols="12" md="3">
                    <v-card variant="outlined" class="h-100">
                      <v-card-text class="text-center">
                        <div class="text-h4 mb-2 text-warning">{{ status.sync_type || '--' }}</div>
                        <div class="text-subtitle-2 text-medium-emphasis">同步类型</div>
                      </v-card-text>
                    </v-card>
                  </v-col>
                </v-row>

                <v-alert type="info" variant="tonal" class="mb-4">
                  <v-alert-title>功能说明</v-alert-title>
                  监听多种事件类型自动同步文件到指定位置，支持多种同步策略，默认启用增量同步，简化配置更易使用。
                </v-alert>

                <v-card variant="outlined">
                  <v-card-title>
                    <v-icon class="mr-2">mdi-play</v-icon>
                    操作控制
                  </v-card-title>
                  <v-card-text>
                    <v-row>
                      <v-col cols="12" sm="6" md="3">
                        <v-btn color="primary" variant="elevated" block prepend-icon="mdi-sync" :loading="syncing" @click="manualSync">
                          手动同步
                        </v-btn>
                      </v-col>
                      <v-col cols="12" sm="6" md="3">
                        <v-btn color="success" variant="elevated" block prepend-icon="mdi-update" :loading="syncing" @click="incrementalSync">
                          增量同步
                        </v-btn>
                      </v-col>
                      <v-col cols="12" sm="6" md="3">
                        <v-btn color="info" variant="outlined" block prepend-icon="mdi-information" :loading="loading" @click="notifyRefresh">
                          刷新状态
                        </v-btn>
                      </v-col>
                      <v-col cols="12" sm="6" md="3">
                        <v-btn color="secondary" variant="outlined" block prepend-icon="mdi-cog" @click="notifySwitch">
                          配置插件
                        </v-btn>
                      </v-col>
                    </v-row>
                  </v-card-text>
                </v-card>
              </v-card-text>

              <v-card-actions class="px-4 pb-4">
                <v-btn variant="outlined" prepend-icon="mdi-close" @click="notifyClose">
                  关闭页面
                </v-btn>
              </v-card-actions>
            </v-card>
          </div>
        \`
      }
    };
  }
};

const __federation_expose_Dashboard__ = {
  get: () => Dashboard,
  init: async () => {
    Dashboard = {
      __esModule: true,
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
              sync_strategy: '',
              sync_type: '',
              last_sync_time: null
            },
            loading: false
          }
        },
        methods: {
          async getStatus() {
            if (!this.allowRefresh) return;

            this.loading = true;
            try {
              const result = await this.api?.get('sync_status');
              if (result && !result.error) {
                this.status = {
                  ...result,
                  last_sync_time: new Date().toLocaleString()
                };
              }
            } catch (error) {
              console.error('获取状态失败:', error);
            } finally {
              this.loading = false;
            }
          },
          formatStrategy(strategy) {
            const strategyMap = {
              'copy': '复制',
              'move': '移动',
              'softlink': '软链接',
              'hardlink': '硬链接'
            };
            return strategyMap[strategy] || strategy;
          },
          formatSyncType(type) {
            const typeMap = {
              'incremental': '增量',
              'full': '全量'
            };
            return typeMap[type] || type;
          }
        },
        mounted() {
          this.getStatus();
          if (this.allowRefresh) {
            setInterval(this.getStatus, 30000);
          }
        },
        template: \`
          <div class="dashboard-widget" style="height: 100%;">
            <v-card class="h-100" style="border-radius: 8px; box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);">
              <v-card-title class="d-flex align-center" style="font-size: 1.1rem; font-weight: 500; padding-bottom: 8px;">
                <v-icon class="mr-2">mdi-sync</v-icon>
                {{ config.title || '整理后同步' }}
                <v-spacer />
                <v-chip :color="status.enabled ? 'success' : 'error'" size="small" variant="flat">
                  {{ status.enabled ? '运行中' : '已停止' }}
                </v-chip>
              </v-card-title>

              <v-card-text style="padding: 12px 16px;" class="pb-2">
                <v-row dense>
                  <v-col cols="6">
                    <div class="text-center">
                      <div class="text-h5 text-primary font-weight-bold">
                        {{ status.sync_paths_count || 0 }}
                      </div>
                      <div class="text-caption text-medium-emphasis">同步路径</div>
                    </div>
                  </v-col>
                  <v-col cols="6">
                    <div class="text-center">
                      <div class="text-h6 text-info">
                        {{ formatStrategy(status.sync_strategy) || '--' }}
                      </div>
                      <div class="text-caption text-medium-emphasis">同步策略</div>
                    </div>
                  </v-col>
                </v-row>

                <v-divider class="my-3" />

                <div class="text-body-2">
                  <div class="d-flex justify-space-between align-center mb-1">
                    <span class="text-medium-emphasis">同步类型:</span>
                    <span class="font-weight-medium">{{ formatSyncType(status.sync_type) || '未设置' }}</span>
                  </div>
                  <div class="d-flex justify-space-between align-center mb-1">
                    <span class="text-medium-emphasis">状态:</span>
                    <v-chip :color="status.enabled ? 'success' : 'grey'" size="x-small" variant="flat">
                      {{ status.enabled ? '启用' : '禁用' }}
                    </v-chip>
                  </div>
                  <div v-if="status.last_sync_time" class="d-flex justify-space-between align-center">
                    <span class="text-medium-emphasis">更新时间:</span>
                    <span class="text-caption">{{ status.last_sync_time }}</span>
                  </div>
                </div>
              </v-card-text>

              <v-card-actions style="padding: 8px 16px 12px;">
                <v-btn size="small" variant="text" prepend-icon="mdi-refresh" :loading="loading" @click="getStatus">
                  刷新
                </v-btn>
                <v-spacer />
                <v-icon :class="status.enabled ? 'text-success' : 'text-grey'" size="20">
                  {{ status.enabled ? 'mdi-check-circle' : 'mdi-pause-circle' }}
                </v-icon>
              </v-card-actions>
            </v-card>
          </div>
        \`
      }
    };
  }
};

const moduleMap = {
  "./Config": __federation_expose_Config__,
  "./Page": __federation_expose_Page__,
  "./Dashboard": __federation_expose_Dashboard__
};

const get = async (module, getScope) => {
  if (moduleMap[module]) {
    await moduleMap[module].init();
    return moduleMap[module].get();
  }
  throw new Error('Module "' + module + '" does not exist in container.');
};

const init = (shareScope, initScope) => {
  return Promise.resolve();
};

// 导出容器接口
const container = { get, init };

// 全局注册
if (typeof window !== 'undefined') {
  window.TransferSync = container;
}

export { get, init };
export default container;