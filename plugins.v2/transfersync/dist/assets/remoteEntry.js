(() => {
  "use strict";
  var __webpack_require__ = {};

  __webpack_require__.d = (exports, definition) => {
    for (var key in definition) {
      if (__webpack_require__.o(definition, key) && !__webpack_require__.o(exports, key)) {
        Object.defineProperty(exports, key, { enumerable: true, get: definition[key] });
      }
    }
  };

  __webpack_require__.o = (obj, prop) => Object.prototype.hasOwnProperty.call(obj, prop);

  __webpack_require__.r = (exports) => {
    if (typeof Symbol !== 'undefined' && Symbol.toStringTag) {
      Object.defineProperty(exports, Symbol.toStringTag, { value: 'Module' });
    }
    Object.defineProperty(exports, '__esModule', { value: true });
  };

  var __webpack_exports__ = {};

  // Config 组件
  const Config = {
    __esModule: true,
    default: {
      name: 'TransferSyncConfig',
      template: `
        <div id="transfersync-config">
          <iframe
            src="/plugin/TransferSync/index.html"
            style="width: 100%; height: 800px; border: none; border-radius: 8px;"
            frameborder="0">
          </iframe>
        </div>
      `
    }
  };

  // Dashboard 组件
  const Dashboard = {
    __esModule: true,
    default: {
      name: 'TransferSyncDashboard',
      template: `
        <div id="transfersync-dashboard">
          <div class="dashboard-container">
            <div class="status-card">
              <h3>整理后同步插件</h3>
              <p>监听多种事件类型自动同步文件到指定位置</p>
              <div class="action-buttons">
                <button class="btn btn-primary" @click="openConfig">
                  <i class="mdi mdi-cog"></i>
                  打开配置
                </button>
                <button class="btn btn-success" @click="manualSync">
                  <i class="mdi mdi-sync"></i>
                  手动同步
                </button>
              </div>
            </div>
          </div>
        </div>
      `,
      methods: {
        openConfig() {
          // 打开配置页面的逻辑
          window.open('/plugin/TransferSync/index.html', '_blank');
        },
        async manualSync() {
          try {
            const response = await fetch('/api/v1/plugin/TransferSync/manual_sync', {
              method: 'POST',
              headers: {
                'Content-Type': 'application/json'
              }
            });
            const result = await response.json();
            if (result.success) {
              this.$message.success(result.message || '手动同步完成');
            } else {
              this.$message.error(result.error || '手动同步失败');
            }
          } catch (error) {
            this.$message.error('网络请求失败: ' + error.message);
          }
        }
      }
    }
  };

  // Page 组件（仪表板页面）
  const Page = {
    __esModule: true,
    default: {
      name: 'TransferSyncPage',
      template: `
        <div id="transfersync-page">
          <iframe
            src="/plugin/TransferSync/index.html"
            style="width: 100%; height: calc(100vh - 120px); border: none; border-radius: 8px;"
            frameborder="0">
          </iframe>
        </div>
      `
    }
  };

  // 导出模块
  __webpack_require__.d(__webpack_exports__, {
    get: () => get
  });

  function get(module) {
    switch (module) {
      case "./Config":
        return Promise.resolve(Config);
      case "./Dashboard":
        return Promise.resolve(Dashboard);
      case "./Page":
        return Promise.resolve(Page);
      default:
        return Promise.reject(new Error("Module not found"));
    }
  }

  // 全局暴露
  var __webpack_module_cache__ = {};
  function __webpack_require__(moduleId) {
    var cachedModule = __webpack_module_cache__[moduleId];
    if (cachedModule !== undefined) {
      return cachedModule.exports;
    }
    var module = __webpack_module_cache__[moduleId] = {
      exports: {}
    };
    return module.exports;
  }

  // 模块联邦入口
  var moduleMap = {
    "./Config": () => Promise.resolve(Config),
    "./Dashboard": () => Promise.resolve(Dashboard),
    "./Page": () => Promise.resolve(Page)
  };

  var get = (module, getScope) => {
    __webpack_require__.R = getScope;
    getScope = __webpack_require__.o(moduleMap, module) ? moduleMap[module]() : Promise.resolve().then(() => {
      throw new Error('Module "' + module + '" does not exist in container.');
    });
    __webpack_require__.R = undefined;
    return getScope;
  };

  var init = (shareScope, initScope) => {
    if (!__webpack_require__.S) __webpack_require__.S = {};
    var name = "TransferSync";
    var oldScope = __webpack_require__.S[name];
    if (oldScope && oldScope !== shareScope) throw new Error("Container initialization failed as it has already been initialized with a different share scope");
    __webpack_require__.S[name] = shareScope;
    return __webpack_require__.I(name, initScope);
  };

  __webpack_require__.I = (name, initScope) => {
    return Promise.resolve();
  };

  // 导出容器接口
  __webpack_exports__ = { get, init };

  // 全局注册
  if (typeof window !== 'undefined') {
    window.TransferSync = __webpack_exports__;
  }

  return __webpack_exports__;
})();