<script setup lang="ts">
import Config from './components/Config.vue'
import Page from './components/Page.vue'
import Dashboard from './components/Dashboard.vue'
import { ref } from 'vue'

// 模拟API对象
const mockApi = {
  get: async (path: string) => {
    console.log('Mock API GET:', path)
    return { success: true, data: {} }
  },
  post: async (path: string, data?: any) => {
    console.log('Mock API POST:', path, data)
    return { success: true, message: '操作成功' }
  }
}

// 当前显示的组件
const currentComponent = ref('Config')

// 模拟配置数据
const mockConfig = {
  title: '整理后同步插件',
  enabled: true,
  source_path: '/downloads',
  target_path: '/media'
}
</script>

<template>
  <div id="app">
    <div style="padding: 20px;">
      <div style="margin-bottom: 20px;">
        <button @click="currentComponent = 'Config'" style="margin-right: 10px;">配置页面</button>
        <button @click="currentComponent = 'Page'" style="margin-right: 10px;">详情页面</button>
        <button @click="currentComponent = 'Dashboard'">仪表板</button>
      </div>

      <Config
        v-if="currentComponent === 'Config'"
        :initial-config="mockConfig"
        :api="mockApi"
      />

      <Page
        v-if="currentComponent === 'Page'"
        :api="mockApi"
      />

      <Dashboard
        v-if="currentComponent === 'Dashboard'"
        :config="mockConfig"
        :api="mockApi"
      />
    </div>
  </div>
</template>

<style>
body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
}
</style>