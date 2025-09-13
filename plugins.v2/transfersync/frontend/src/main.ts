// 主入口文件 - 用于开发环境
import { createApp } from 'vue'
import App from './App.vue'

// 开发环境下的应用初始化
if (import.meta.env.DEV) {
  const app = createApp(App)
  app.mount('#app')
}

// 导出组件供模块联邦使用
export { default as Config } from './components/Config.vue'
export { default as Page } from './components/Page.vue'
export { default as Dashboard } from './components/Dashboard.vue'