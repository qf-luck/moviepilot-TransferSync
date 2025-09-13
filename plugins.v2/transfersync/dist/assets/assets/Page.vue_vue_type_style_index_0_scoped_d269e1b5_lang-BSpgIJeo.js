import { importShared } from './__federation_fn_import-JrT3xvdd.js';

const {defineComponent:_defineComponent} = await importShared('vue');

const {createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode,toDisplayString:_toDisplayString,normalizeClass:_normalizeClass,createElementVNode:_createElementVNode,openBlock:_openBlock,createElementBlock:_createElementBlock} = await importShared('vue');

const _hoisted_1 = { class: "plugin-page pa-4" };
const _hoisted_2 = { class: "text-h4 mb-2 text-primary" };
const _hoisted_3 = { class: "text-h4 mb-2 text-info" };
const _hoisted_4 = { class: "text-h4 mb-2 text-warning" };
const {ref,onMounted} = await importShared('vue');

const _sfc_main = /* @__PURE__ */ _defineComponent({
  __name: "Page",
  props: {
    api: {
      type: Object,
      default: () => {
      }
    }
  },
  emits: ["action", "switch", "close"],
  setup(__props, { emit: __emit }) {
    const emit = __emit;
    const props = __props;
    const status = ref({
      enabled: false,
      sync_paths_count: 0,
      sync_strategy: "",
      sync_type: "",
      delay_minutes: 0
    });
    const loading = ref(false);
    const syncing = ref(false);
    async function getStatus() {
      loading.value = true;
      try {
        const result = await props.api.get("sync_status");
        if (result && !result.error) {
          status.value = result;
        }
      } catch (error) {
        console.error("获取状态失败:", error);
      } finally {
        loading.value = false;
      }
    }
    async function manualSync() {
      syncing.value = true;
      try {
        const result = await props.api.post("manual_sync");
        if (result.success) {
          console.log("手动同步完成:", result.message);
          await getStatus();
        } else {
          console.error("手动同步失败:", result.error);
        }
      } catch (error) {
        console.error("手动同步异常:", error);
      } finally {
        syncing.value = false;
      }
    }
    async function incrementalSync() {
      syncing.value = true;
      try {
        const result = await props.api.post("incremental_sync");
        if (result.success) {
          console.log("增量同步完成:", result.message);
          await getStatus();
        } else {
          console.error("增量同步失败:", result.error);
        }
      } catch (error) {
        console.error("增量同步异常:", error);
      } finally {
        syncing.value = false;
      }
    }
    function notifyRefresh() {
      getStatus();
      emit("action");
    }
    function notifySwitch() {
      emit("switch");
    }
    function notifyClose() {
      emit("close");
    }
    onMounted(() => {
      getStatus();
    });
    return (_ctx, _cache) => {
      const _component_v_icon = _resolveComponent("v-icon");
      const _component_v_spacer = _resolveComponent("v-spacer");
      const _component_v_chip = _resolveComponent("v-chip");
      const _component_v_card_title = _resolveComponent("v-card-title");
      const _component_v_card_text = _resolveComponent("v-card-text");
      const _component_v_card = _resolveComponent("v-card");
      const _component_v_col = _resolveComponent("v-col");
      const _component_v_row = _resolveComponent("v-row");
      const _component_v_alert_title = _resolveComponent("v-alert-title");
      const _component_v_alert = _resolveComponent("v-alert");
      const _component_v_btn = _resolveComponent("v-btn");
      const _component_v_expansion_panel_title = _resolveComponent("v-expansion-panel-title");
      const _component_v_list_item_title = _resolveComponent("v-list-item-title");
      const _component_v_list_item_subtitle = _resolveComponent("v-list-item-subtitle");
      const _component_v_list_item = _resolveComponent("v-list-item");
      const _component_v_list = _resolveComponent("v-list");
      const _component_v_expansion_panel_text = _resolveComponent("v-expansion-panel-text");
      const _component_v_expansion_panel = _resolveComponent("v-expansion-panel");
      const _component_v_expansion_panels = _resolveComponent("v-expansion-panels");
      const _component_v_card_actions = _resolveComponent("v-card-actions");
      return _openBlock(), _createElementBlock("div", _hoisted_1, [
        _createVNode(_component_v_card, null, {
          default: _withCtx(() => [
            _createVNode(_component_v_card_title, { class: "d-flex align-center" }, {
              default: _withCtx(() => [
                _createVNode(_component_v_icon, { class: "mr-2" }, {
                  default: _withCtx(() => [..._cache[0] || (_cache[0] = [
                    _createTextVNode("mdi-sync", -1)
                  ])]),
                  _: 1
                }),
                _cache[1] || (_cache[1] = _createTextVNode(" 整理后同步插件 ", -1)),
                _createVNode(_component_v_spacer),
                _createVNode(_component_v_chip, {
                  color: status.value.enabled ? "success" : "error",
                  size: "small",
                  variant: "flat"
                }, {
                  default: _withCtx(() => [
                    _createTextVNode(_toDisplayString(status.value.enabled ? "已启用" : "已禁用"), 1)
                  ]),
                  _: 1
                }, 8, ["color"])
              ]),
              _: 1
            }),
            _createVNode(_component_v_card_text, null, {
              default: _withCtx(() => [
                _createVNode(_component_v_row, { class: "mb-4" }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "3"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_card, {
                          variant: "outlined",
                          class: "h-100"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_card_text, { class: "text-center" }, {
                              default: _withCtx(() => [
                                _createElementVNode("div", {
                                  class: _normalizeClass(["text-h4 mb-2", status.value.enabled ? "text-success" : "text-error"])
                                }, _toDisplayString(status.value.enabled ? "已启用" : "已禁用"), 3),
                                _cache[2] || (_cache[2] = _createElementVNode("div", { class: "text-subtitle-2 text-medium-emphasis" }, " 插件状态 ", -1))
                              ]),
                              _: 1
                            })
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "3"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_card, {
                          variant: "outlined",
                          class: "h-100"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_card_text, { class: "text-center" }, {
                              default: _withCtx(() => [
                                _createElementVNode("div", _hoisted_2, _toDisplayString(status.value.sync_paths_count || 0), 1),
                                _cache[3] || (_cache[3] = _createElementVNode("div", { class: "text-subtitle-2 text-medium-emphasis" }, " 同步路径数量 ", -1))
                              ]),
                              _: 1
                            })
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "3"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_card, {
                          variant: "outlined",
                          class: "h-100"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_card_text, { class: "text-center" }, {
                              default: _withCtx(() => [
                                _createElementVNode("div", _hoisted_3, _toDisplayString(status.value.sync_strategy || "--"), 1),
                                _cache[4] || (_cache[4] = _createElementVNode("div", { class: "text-subtitle-2 text-medium-emphasis" }, " 当前同步策略 ", -1))
                              ]),
                              _: 1
                            })
                          ]),
                          _: 1
                        })
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_col, {
                      cols: "12",
                      md: "3"
                    }, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_card, {
                          variant: "outlined",
                          class: "h-100"
                        }, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_card_text, { class: "text-center" }, {
                              default: _withCtx(() => [
                                _createElementVNode("div", _hoisted_4, _toDisplayString(status.value.sync_type || "--"), 1),
                                _cache[5] || (_cache[5] = _createElementVNode("div", { class: "text-subtitle-2 text-medium-emphasis" }, " 同步类型 ", -1))
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
                _createVNode(_component_v_alert, {
                  type: "info",
                  variant: "tonal",
                  class: "mb-4"
                }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_alert_title, null, {
                      default: _withCtx(() => [..._cache[6] || (_cache[6] = [
                        _createTextVNode("功能说明", -1)
                      ])]),
                      _: 1
                    }),
                    _cache[7] || (_cache[7] = _createTextVNode(" 监听多种事件类型自动同步文件到指定位置，支持多种同步策略，默认启用增量同步，简化配置更易使用。 ", -1))
                  ]),
                  _: 1
                }),
                _createVNode(_component_v_card, { variant: "outlined" }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_card_title, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_icon, { class: "mr-2" }, {
                          default: _withCtx(() => [..._cache[8] || (_cache[8] = [
                            _createTextVNode("mdi-play", -1)
                          ])]),
                          _: 1
                        }),
                        _cache[9] || (_cache[9] = _createTextVNode(" 操作控制 ", -1))
                      ]),
                      _: 1
                    }),
                    _createVNode(_component_v_card_text, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_row, null, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_col, {
                              cols: "12",
                              sm: "6",
                              md: "3"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_btn, {
                                  color: "primary",
                                  variant: "elevated",
                                  block: "",
                                  "prepend-icon": "mdi-sync",
                                  loading: syncing.value,
                                  onClick: manualSync
                                }, {
                                  default: _withCtx(() => [..._cache[10] || (_cache[10] = [
                                    _createTextVNode(" 手动同步 ", -1)
                                  ])]),
                                  _: 1
                                }, 8, ["loading"])
                              ]),
                              _: 1
                            }),
                            _createVNode(_component_v_col, {
                              cols: "12",
                              sm: "6",
                              md: "3"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_btn, {
                                  color: "success",
                                  variant: "elevated",
                                  block: "",
                                  "prepend-icon": "mdi-update",
                                  loading: syncing.value,
                                  onClick: incrementalSync
                                }, {
                                  default: _withCtx(() => [..._cache[11] || (_cache[11] = [
                                    _createTextVNode(" 增量同步 ", -1)
                                  ])]),
                                  _: 1
                                }, 8, ["loading"])
                              ]),
                              _: 1
                            }),
                            _createVNode(_component_v_col, {
                              cols: "12",
                              sm: "6",
                              md: "3"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_btn, {
                                  color: "info",
                                  variant: "outlined",
                                  block: "",
                                  "prepend-icon": "mdi-information",
                                  loading: loading.value,
                                  onClick: notifyRefresh
                                }, {
                                  default: _withCtx(() => [..._cache[12] || (_cache[12] = [
                                    _createTextVNode(" 刷新状态 ", -1)
                                  ])]),
                                  _: 1
                                }, 8, ["loading"])
                              ]),
                              _: 1
                            }),
                            _createVNode(_component_v_col, {
                              cols: "12",
                              sm: "6",
                              md: "3"
                            }, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_btn, {
                                  color: "secondary",
                                  variant: "outlined",
                                  block: "",
                                  "prepend-icon": "mdi-cog",
                                  onClick: notifySwitch
                                }, {
                                  default: _withCtx(() => [..._cache[13] || (_cache[13] = [
                                    _createTextVNode(" 配置插件 ", -1)
                                  ])]),
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
                    })
                  ]),
                  _: 1
                }),
                _createVNode(_component_v_expansion_panels, { class: "mt-4" }, {
                  default: _withCtx(() => [
                    _createVNode(_component_v_expansion_panel, null, {
                      default: _withCtx(() => [
                        _createVNode(_component_v_expansion_panel_title, null, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_icon, { class: "mr-2" }, {
                              default: _withCtx(() => [..._cache[14] || (_cache[14] = [
                                _createTextVNode("mdi-information-outline", -1)
                              ])]),
                              _: 1
                            }),
                            _cache[15] || (_cache[15] = _createTextVNode(" 详细配置信息 ", -1))
                          ]),
                          _: 1
                        }),
                        _createVNode(_component_v_expansion_panel_text, null, {
                          default: _withCtx(() => [
                            _createVNode(_component_v_list, null, {
                              default: _withCtx(() => [
                                _createVNode(_component_v_list_item, null, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list_item_title, null, {
                                      default: _withCtx(() => [..._cache[16] || (_cache[16] = [
                                        _createTextVNode("同步策略", -1)
                                      ])]),
                                      _: 1
                                    }),
                                    _createVNode(_component_v_list_item_subtitle, null, {
                                      default: _withCtx(() => [
                                        _createTextVNode(_toDisplayString(status.value.sync_strategy || "未设置"), 1)
                                      ]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                }),
                                _createVNode(_component_v_list_item, null, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list_item_title, null, {
                                      default: _withCtx(() => [..._cache[17] || (_cache[17] = [
                                        _createTextVNode("同步类型", -1)
                                      ])]),
                                      _: 1
                                    }),
                                    _createVNode(_component_v_list_item_subtitle, null, {
                                      default: _withCtx(() => [
                                        _createTextVNode(_toDisplayString(status.value.sync_type || "未设置"), 1)
                                      ]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                }),
                                _createVNode(_component_v_list_item, null, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list_item_title, null, {
                                      default: _withCtx(() => [..._cache[18] || (_cache[18] = [
                                        _createTextVNode("延迟时间", -1)
                                      ])]),
                                      _: 1
                                    }),
                                    _createVNode(_component_v_list_item_subtitle, null, {
                                      default: _withCtx(() => [
                                        _createTextVNode(_toDisplayString(status.value.delay_minutes || 0) + " 分钟", 1)
                                      ]),
                                      _: 1
                                    })
                                  ]),
                                  _: 1
                                }),
                                _createVNode(_component_v_list_item, null, {
                                  default: _withCtx(() => [
                                    _createVNode(_component_v_list_item_title, null, {
                                      default: _withCtx(() => [..._cache[19] || (_cache[19] = [
                                        _createTextVNode("配置路径数", -1)
                                      ])]),
                                      _: 1
                                    }),
                                    _createVNode(_component_v_list_item_subtitle, null, {
                                      default: _withCtx(() => [
                                        _createTextVNode(_toDisplayString(status.value.sync_paths_count || 0) + " 个", 1)
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
                  variant: "outlined",
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
