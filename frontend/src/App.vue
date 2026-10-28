<script setup lang="ts">
/**
 * 应用外壳:顶部导航(三页) + 后端健康状态 + 路由出口。
 * 健康检查走 GET /healthz(docs/api.md §1);status != ok 就给黄条,不是 ok 也不挡页面。
 */
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { RouterLink, RouterView } from 'vue-router'

import { getHealth } from './api/health'
import { describeApiError } from './api/http'

const HEALTH_INTERVAL_MS = 10_000

/** 首帧先当作正常,别一进页面就闪一下黄条 */
const backendOk = ref(true)
const backendHint = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
/** 卸载后别再排下一次探测,避免遗留定时器 */
let stopped = false

async function probe(): Promise<void> {
  try {
    const health = await getHealth()
    backendOk.value = health.status === 'ok'
    backendHint.value = backendOk.value
      ? ''
      : `status=${health.status} · db=${health.db} · redis=${health.redis}`
  } catch (error) {
    backendOk.value = false
    backendHint.value = describeApiError(error)
  } finally {
    if (!stopped) {
      timer = setTimeout(() => {
        void probe()
      }, HEALTH_INTERVAL_MS)
    }
  }
}

onMounted(() => {
  void probe()
})

onBeforeUnmount(() => {
  stopped = true
  if (timer !== undefined) clearTimeout(timer)
})
</script>

<template>
  <div class="app-shell">
    <header class="topbar">
      <div class="topbar-inner">
        <RouterLink class="brand" :to="{ name: 'import' }">baleen</RouterLink>
        <nav class="nav">
          <RouterLink class="nav-link" :to="{ name: 'import' }" active-class="nav-link--active">
            导入
          </RouterLink>
          <RouterLink class="nav-link" :to="{ name: 'notes' }" active-class="nav-link--active">
            笔记库
          </RouterLink>
          <RouterLink class="nav-link" :to="{ name: 'search' }" active-class="nav-link--active">
            检索
          </RouterLink>
        </nav>
        <span class="health" :class="backendOk ? 'health--ok' : 'health--bad'">
          <i class="health-dot" />
          {{ backendOk ? '后端正常' : '后端未就绪' }}
        </span>
      </div>
    </header>

    <div v-if="!backendOk" class="banner banner--warn" role="status">
      <strong>后端未就绪</strong>
      <span v-if="backendHint !== ''" class="banner-hint">{{ backendHint }}</span>
    </div>

    <main class="container">
      <RouterView />
    </main>
  </div>
</template>
