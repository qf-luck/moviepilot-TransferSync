<script setup lang="ts">
// 开发环境的测试组件
import Config from './components/Config.vue'
import { ref } from 'vue'

const testConfig = ref({
  enabled: true,
  source_path: '/downloads',
  target_path: '/media',
  sync_strategy: 'copy'
})

const mockApi = {
  get: async (path: string) => {
    console.log('Mock API GET:', path)
    return { success: true, data: testConfig.value }
  },
  post: async (path: string, data: any) => {
    console.log('Mock API POST:', path, data)
    return { success: true }
  }
}
</script>

<template>
  <div id="app">
    <h1>TransferSync Plugin - Development Mode</h1>
    <Config :api="mockApi" :initial-config="testConfig" />
  </div>
</template>

<style>
body {
  margin: 0;
  font-family: 'Roboto', sans-serif;
}
</style>