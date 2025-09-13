import { createApp } from 'vue'
import App from './App.vue'

// 开发环境下创建应用实例
if (import.meta.env.MODE === 'development') {
  createApp(App).mount('#app')
}