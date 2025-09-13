import { importShared } from './__federation_fn_import-JrT3xvdd.js';

const {defineComponent:_defineComponent} = await importShared('vue');

const {createTextVNode:_createTextVNode,resolveComponent:_resolveComponent,withCtx:_withCtx,createVNode:_createVNode,toDisplayString:_toDisplayString,createElementVNode:_createElementVNode,openBlock:_openBlock,createBlock:_createBlock,createCommentVNode:_createCommentVNode} = await importShared('vue');

const _hoisted_1 = { class: "text-center" };
const _hoisted_2 = { class: "text-h6 text-primary" };
const _hoisted_3 = { class: "text-center" };
const _hoisted_4 = { class: "text-subtitle-2" };
const _hoisted_5 = { class: "text-center" };
const _hoisted_6 = { class: "text-subtitle-2 text-info" };
const _hoisted_7 = { class: "text-caption text-center text-medium-emphasis" };
const {ref,onMounted,onUnmounted} = await importShared('vue');

const _sfc_main = /* @__PURE__ */ _defineComponent({
  __name: "Dashboard",
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
      default: () => {
      }
    }
  },
  setup(__props) {
    const props = __props;
    const status = ref({
      enabled: false,
      sync_paths_count: 0,
      sync_strategy: "",
      last_sync_time: "",
      total_synced_files: 0
    });
    const loading = ref(false);
    let refreshInterval = null;
    async function getStatus() {
      if (!props.allowRefresh || !props.api) return;
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
    onMounted(() => {
      getStatus();
      if (props.allowRefresh) {
        refreshInterval = setInterval(getStatus, 3e4);
      }
    });
    onUnmounted(() => {
      if (refreshInterval) {
        clearInterval(refreshInterval);
      }
    });
    return (_ctx, _cache) => {
      const _component_v_icon = _resolveComponent("v-icon");
      const _component_v_spacer = _resolveComponent("v-spacer");
      const _component_v_chip = _resolveComponent("v-chip");
      const _component_v_card_title = _resolveComponent("v-card-title");
      const _component_v_col = _resolveComponent("v-col");
      const _component_v_row = _resolveComponent("v-row");
      const _component_v_card_text = _resolveComponent("v-card-text");
      const _component_v_btn = _resolveComponent("v-btn");
      const _component_v_card_actions = _resolveComponent("v-card-actions");
      const _component_v_card = _resolveComponent("v-card");
      return _openBlock(), _createBlock(_component_v_card, { class: "dashboard-widget" }, {
        default: _withCtx(() => [
          _createVNode(_component_v_card_title, { class: "d-flex align-center" }, {
            default: _withCtx(() => [
              _createVNode(_component_v_icon, {
                class: "mr-2",
                size: "small"
              }, {
                default: _withCtx(() => [..._cache[0] || (_cache[0] = [
                  _createTextVNode("mdi-sync", -1)
                ])]),
                _: 1
              }),
              _createTextVNode(" " + _toDisplayString(__props.config.title || "整理后同步") + " ", 1),
              _createVNode(_component_v_spacer),
              _createVNode(_component_v_chip, {
                color: status.value.enabled ? "success" : "error",
                size: "x-small",
                variant: "flat"
              }, {
                default: _withCtx(() => [
                  _createTextVNode(_toDisplayString(status.value.enabled ? "运行" : "停止"), 1)
                ]),
                _: 1
              }, 8, ["color"])
            ]),
            _: 1
          }),
          _createVNode(_component_v_card_text, { class: "pb-2" }, {
            default: _withCtx(() => [
              _createVNode(_component_v_row, { dense: "" }, {
                default: _withCtx(() => [
                  _createVNode(_component_v_col, { cols: "6" }, {
                    default: _withCtx(() => [
                      _createElementVNode("div", _hoisted_1, [
                        _createElementVNode("div", _hoisted_2, _toDisplayString(status.value.sync_paths_count || 0), 1),
                        _cache[1] || (_cache[1] = _createElementVNode("div", { class: "text-caption" }, "同步路径", -1))
                      ])
                    ]),
                    _: 1
                  }),
                  _createVNode(_component_v_col, { cols: "6" }, {
                    default: _withCtx(() => [
                      _createElementVNode("div", _hoisted_3, [
                        _createElementVNode("div", _hoisted_4, _toDisplayString(status.value.sync_strategy || "--"), 1),
                        _cache[2] || (_cache[2] = _createElementVNode("div", { class: "text-caption" }, "同步策略", -1))
                      ])
                    ]),
                    _: 1
                  })
                ]),
                _: 1
              }),
              status.value.total_synced_files > 0 ? (_openBlock(), _createBlock(_component_v_row, {
                key: 0,
                dense: "",
                class: "mt-2"
              }, {
                default: _withCtx(() => [
                  _createVNode(_component_v_col, { cols: "12" }, {
                    default: _withCtx(() => [
                      _createElementVNode("div", _hoisted_5, [
                        _createElementVNode("div", _hoisted_6, _toDisplayString(status.value.total_synced_files), 1),
                        _cache[3] || (_cache[3] = _createElementVNode("div", { class: "text-caption" }, "已同步文件", -1))
                      ])
                    ]),
                    _: 1
                  })
                ]),
                _: 1
              })) : _createCommentVNode("", true),
              status.value.last_sync_time ? (_openBlock(), _createBlock(_component_v_row, {
                key: 1,
                dense: "",
                class: "mt-2"
              }, {
                default: _withCtx(() => [
                  _createVNode(_component_v_col, { cols: "12" }, {
                    default: _withCtx(() => [
                      _createElementVNode("div", _hoisted_7, " 上次同步：" + _toDisplayString(status.value.last_sync_time), 1)
                    ]),
                    _: 1
                  })
                ]),
                _: 1
              })) : _createCommentVNode("", true)
            ]),
            _: 1
          }),
          _createVNode(_component_v_card_actions, { class: "pt-0" }, {
            default: _withCtx(() => [
              _createVNode(_component_v_btn, {
                size: "small",
                variant: "text",
                loading: loading.value,
                onClick: getStatus
              }, {
                default: _withCtx(() => [..._cache[4] || (_cache[4] = [
                  _createTextVNode(" 刷新 ", -1)
                ])]),
                _: 1
              }, 8, ["loading"])
            ]),
            _: 1
          })
        ]),
        _: 1
      });
    };
  }
});

export { _sfc_main as _ };
