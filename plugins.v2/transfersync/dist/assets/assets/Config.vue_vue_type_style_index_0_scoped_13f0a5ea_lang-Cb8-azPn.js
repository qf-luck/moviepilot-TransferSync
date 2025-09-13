import { importShared } from './__federation_fn_import-JrT3xvdd.js';

const {defineComponent:_defineComponent} = await importShared('vue');

const {createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode,createElementBlock:_createElementBlock} = await importShared('vue');

const _hoisted_1 = { class: "plugin-config pa-4" };
const {ref,onMounted} = await importShared('vue');

const _sfc_main = /* @__PURE__ */ _defineComponent({
  __name: "Config",
  props: {
    initialConfig: {
      type: Object,
      default: () => ({})
    },
    api: {
      type: Object,
      default: () => {
      }
    }
  },
  emits: ["save", "close", "switch"],
  setup(__props, { emit: __emit }) {
    const props = __props;
    const config = ref({
      enabled: false,
      source_path: "",
      target_path: "",
      sync_type: "incremental",
      execution_mode: "immediate",
      delay_minutes: 5,
      enable_notifications: false,
      notification_channel: "telegram",
      sync_strategy: "copy",
      max_depth: -1,
      file_filters: "",
      exclude_patterns: "",
      max_workers: 4,
      trigger_events: ["transfer.complete"],
      ...props.initialConfig
    });
    const emit = __emit;
    const loading = ref(false);
    const saving = ref(false);
    const notificationChannels = [
      { title: "Telegram", value: "telegram" },
      { title: "微信", value: "wechat" },
      { title: "QQ", value: "qq" },
      { title: "钉钉", value: "dingtalk" },
      { title: "Slack", value: "slack" },
      { title: "Discord", value: "discord" },
      { title: "Bark", value: "bark" },
      { title: "PushPlus", value: "pushplus" },
      { title: "Gotify", value: "gotify" },
      { title: "Pushover", value: "pushover" },
      { title: "Server酱", value: "serverchan" },
      { title: "WebHook", value: "webhook" }
    ];
    const syncStrategies = [
      { title: "复制文件", value: "copy" },
      { title: "移动文件", value: "move" },
      { title: "软链接", value: "softlink" },
      { title: "硬链接", value: "hardlink" }
    ];
    const syncTypes = [
      { title: "增量同步", value: "incremental" },
      { title: "全量同步", value: "full" }
    ];
    const executionModes = [
      { title: "立即执行", value: "immediate" },
      { title: "延迟执行", value: "delayed" }
    ];
    const triggerEventOptions = [
      { title: "整理完成", value: "transfer.complete" },
      { title: "下载添加", value: "download.added" },
      { title: "订阅完成", value: "subscribe.complete" },
      { title: "媒体添加", value: "media.added" },
      { title: "文件移动", value: "file.moved" },
      { title: "目录扫描完成", value: "directory.scan.complete" },
      { title: "刮削完成", value: "scrape.complete" },
      { title: "插件触发", value: "plugin.triggered" }
    ];
    async function saveConfig() {
      saving.value = true;
      try {
        const result = await props.api.post("save_config_api", config.value);
        if (result.success) {
          emit("save", config.value);
        } else {
          console.error("保存配置失败:", result.error);
        }
      } catch (error) {
        console.error("保存配置异常:", error);
      } finally {
        saving.value = false;
      }
    }
    async function testConfig() {
      loading.value = true;
      try {
        const result = await props.api.get("test_paths");
        if (result.success) {
          console.log("路径测试成功:", result.results);
        } else {
          console.error("路径测试失败:", result.error);
        }
      } catch (error) {
        console.error("路径测试异常:", error);
      } finally {
        loading.value = false;
      }
    }
    function notifySwitch() {
      emit("switch");
    }
    function notifyClose() {
      emit("close");
    }
    async function loadConfig() {
      loading.value = true;
      try {
        const result = await props.api.get("get_config_api");
        if (result.success && result.data) {
          Object.assign(config.value, result.data);
        }
      } catch (error) {
        console.error("加载配置失败:", error);
      } finally {
        loading.value = false;
      }
    }
    onMounted(() => {
      loadConfig();
    });
    return (_ctx, _cache) => {
      const _component_v_icon = _resolveComponent("v-icon");
      const _component_v_card_title = _resolveComponent("v-card-title");
      const _component_v_alert = _resolveComponent("v-alert");
      const _component_v_switch = _resolveComponent("v-switch");
      const _component_v_col = _resolveComponent("v-col");
      const _component_v_row = _resolveComponent("v-row");
      const _component_v_select = _resolveComponent("v-select");
      const _component_v_text_field = _resolveComponent("v-text-field");
      const _component_v_form = _resolveComponent("v-form");
      const _component_v_card_text = _resolveComponent("v-card-text");
      const _component_v_btn = _resolveComponent("v-btn");
      const _component_v_spacer = _resolveComponent("v-spacer");
      const _component_v_card_actions = _resolveComponent("v-card-actions");
      const _component_v_card = _resolveComponent("v-card");
      return _openBlock(), _createElementBlock("div", _hoisted_1, [
        _createVNode(_component_v_card, null, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_title, { class: "d-flex align-center" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_icon, { class: "mr-2" }, {
                  default: _withCtx(() => [..._cache[14] || (_cache[14] = [
                    _createTextVNode("mdi-sync", -1)
                  ])]),
                  _: 1
                }),
                _cache[15] || (_cache[15] = _createTextVNode(" 整理后同步插件配置 ", -1))
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_text, null, {
              default: _withCtx(() => [
                _createVNode(_component_v_alert, {
                  type: "info",
                  variant: "tonal",
                  class: "mb-4"
                }, {
                  default: _withCtx(() => [..._cache[16] || (_cache[16] = [
                    _createTextVNode(" 监听多种事件类型自动同步文件到指定位置，支持多种同步策略，默认启用增量同步 ", -1)
                  ])]),
                  _: 1
                }),
                _createVNode(_component_v_form, null, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_row, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_switch, {
                              modelValue: config.value.enabled,
                              "onUpdate:modelValue": _cache[0] || (_cache[0] = ($event) => config.value.enabled = $event),
                              label: "启用插件",
                              color: "primary",
                              "hide-details": ""
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_switch, {
                              modelValue: config.value.enable_notifications,
                              "onUpdate:modelValue": _cache[1] || (_cache[1] = ($event) => config.value.enable_notifications = $event),
                              label: "启用通知",
                              color: "primary",
                              "hide-details": ""
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    config.value.enable_notifications ? (_openBlock(), _createBlock(_component_v_row, { key: 0 }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_select, {
                              modelValue: config.value.notification_channel,
                              "onUpdate:modelValue": _cache[2] || (_cache[2] = ($event) => config.value.notification_channel = $event),
                              items: notificationChannels,
                              label: "通知渠道",
                              "prepend-inner-icon": "mdi-bell",
                              variant: "outlined"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    })) : _createCommentVNode("", true),
                    _createVNode(_component_v_row, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_text_field, {
                              modelValue: config.value.source_path,
                              "onUpdate:modelValue": _cache[3] || (_cache[3] = ($event) => config.value.source_path = $event),
                              label: "源路径",
                              placeholder: "请输入源路径，例如：/downloads",
                              "prepend-inner-icon": "mdi-folder-outline",
                              variant: "outlined"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_text_field, {
                              modelValue: config.value.target_path,
                              "onUpdate:modelValue": _cache[4] || (_cache[4] = ($event) => config.value.target_path = $event),
                              label: "目标路径",
                              placeholder: "请输入目标路径，例如：/media",
                              "prepend-inner-icon": "mdi-folder",
                              variant: "outlined"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_row, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "3"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_select, {
                              modelValue: config.value.sync_type,
                              "onUpdate:modelValue": _cache[5] || (_cache[5] = ($event) => config.value.sync_type = $event),
                              items: syncTypes,
                              label: "同步类型",
                              "prepend-inner-icon": "mdi-sync",
                              variant: "outlined"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "3"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_select, {
                              modelValue: config.value.execution_mode,
                              "onUpdate:modelValue": _cache[6] || (_cache[6] = ($event) => config.value.execution_mode = $event),
                              items: executionModes,
                              label: "执行模式",
                              "prepend-inner-icon": "mdi-clock-outline",
                              variant: "outlined"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "3"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_text_field, {
                              modelValue: config.value.delay_minutes,
                              "onUpdate:modelValue": _cache[7] || (_cache[7] = ($event) => config.value.delay_minutes = $event),
                              modelModifiers: { number: true },
                              label: "延迟时间（分钟）",
                              type: "number",
                              disabled: config.value.execution_mode !== "delayed",
                              "prepend-inner-icon": "mdi-timer",
                              variant: "outlined",
                              min: "1"
                            }, null, 8, ["modelValue", "disabled"])
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "3"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_select, {
                              modelValue: config.value.sync_strategy,
                              "onUpdate:modelValue": _cache[8] || (_cache[8] = ($event) => config.value.sync_strategy = $event),
                              items: syncStrategies,
                              label: "文件操作",
                              "prepend-inner-icon": "mdi-file-move",
                              variant: "outlined"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_row, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, { cols: "12" }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_select, {
                              modelValue: config.value.trigger_events,
                              "onUpdate:modelValue": _cache[9] || (_cache[9] = ($event) => config.value.trigger_events = $event),
                              items: triggerEventOptions,
                              label: "触发事件",
                              multiple: "",
                              chips: "",
                              "prepend-inner-icon": "mdi-lightning-bolt",
                              variant: "outlined",
                              hint: "选择触发同步的事件类型",
                              "persistent-hint": ""
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_row, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_text_field, {
                              modelValue: config.value.file_filters,
                              "onUpdate:modelValue": _cache[10] || (_cache[10] = ($event) => config.value.file_filters = $event),
                              label: "文件类型过滤",
                              placeholder: "mp4,mkv,avi,mov",
                              "prepend-inner-icon": "mdi-filter",
                              variant: "outlined",
                              hint: "只同步指定类型的文件，用逗号分隔",
                              "persistent-hint": ""
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_text_field, {
                              modelValue: config.value.exclude_patterns,
                              "onUpdate:modelValue": _cache[11] || (_cache[11] = ($event) => config.value.exclude_patterns = $event),
                              label: "排除模式",
                              placeholder: "temp,cache,@eaDir",
                              "prepend-inner-icon": "mdi-filter-remove",
                              variant: "outlined",
                              hint: "排除包含这些字符的文件/目录",
                              "persistent-hint": ""
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_row, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_text_field, {
                              modelValue: config.value.max_depth,
                              "onUpdate:modelValue": _cache[12] || (_cache[12] = ($event) => config.value.max_depth = $event),
                              modelModifiers: { number: true },
                              label: "最大目录深度",
                              type: "number",
                              placeholder: "-1表示无限制",
                              "prepend-inner-icon": "mdi-file-tree",
                              variant: "outlined"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_col, {
                          cols: "12",
                          md: "6"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_text_field, {
                              modelValue: config.value.max_workers,
                              "onUpdate:modelValue": _cache[13] || (_cache[13] = ($event) => config.value.max_workers = $event),
                              modelModifiers: { number: true },
                              label: "并发线程数",
                              type: "number",
                              "prepend-inner-icon": "mdi-memory",
                              variant: "outlined",
                              min: "1",
                              max: "16"
                            }, null, 8, ["modelValue"])
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    })
                  ]),
                  _: 1
                })
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_actions, { class: "px-4 pb-4" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_btn, {
                  color: "primary",
                  variant: "elevated",
                  "prepend-icon": "mdi-content-save",
                  loading: saving.value,
                  onClick: saveConfig
                }, {
                  default: _withCtx(() => [..._cache[17] || (_cache[17] = [
                    _createTextVNode(" 保存配置 ", -1)
                  ])]),
                  _: 1
                }, 8, ["loading"]),
                _createVNode(_component_v_btn, {
                  color: "secondary",
                  variant: "outlined",
                  "prepend-icon": "mdi-test-tube",
                  loading: loading.value,
                  onClick: testConfig
                }, {
                  default: _withCtx(() => [..._cache[18] || (_cache[18] = [
                    _createTextVNode(" 测试配置 ", -1)
                  ])]),
                  _: 1
                }, 8, ["loading"]),
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_btn, {
                  variant: "outlined",
                  "prepend-icon": "mdi-view-dashboard",
                  onClick: notifySwitch
                }, {
                  default: _withCtx(() => [..._cache[19] || (_cache[19] = [
                    _createTextVNode(" 切换到详情页面 ", -1)
                  ])]),
                  _: 1
                }),
                _createVNode(_component_v_btn, {
                  variant: "text",
                  "prepend-icon": "mdi-close",
                  onClick: notifyClose
                }, {
                  default: _withCtx(() => [..._cache[20] || (_cache[20] = [
                    _createTextVNode(" 关闭页面 ", -1)
                  ])]),
                  _: 1
                })
              ]),
              _: 1
            })
          ]),
          _: 1
        })
      ]);
    };
  }
});

export { _sfc_main as _ };
