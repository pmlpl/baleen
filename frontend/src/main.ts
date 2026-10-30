import { createPinia } from 'pinia'
import { createApp } from 'vue'

import App from './App.vue'
import { setUnauthorizedHandler } from './api/http'
import { resetGate } from './onboardingGate'
import router from './router'
import { useAuthStore } from './stores/auth'
import './styles.css'

const pinia = createPinia()
const app = createApp(App).use(pinia).use(router)

/**
 * 401 统一处理:token 过期/失效时清状态并回登录页。
 * 注册在这里(而不是 http.ts 里直接 import router)是为了避免循环依赖。
 */
setUnauthorizedHandler(() => {
  useAuthStore(pinia).logout()
  resetGate()
  if (router.currentRoute.value.name !== 'login') {
    void router.push({ name: 'login' })
  }
})

app.mount('#app')

// 启动时恢复登录态(token 在 localStorage);失败由 store 内部当作未登录
void useAuthStore(pinia).restore()
