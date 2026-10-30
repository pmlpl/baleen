<script setup lang="ts">
/**
 * 应用外壳:顶部导航(三页) + 后端健康状态 + 路由出口。
 * 健康检查走 GET /healthz(docs/api.md §1);status != ok 就给黄条,不是 ok 也不挡页面。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute } from 'vue-router'

import { getHealth } from './api/health'
import { describeApiError } from './api/http'
import { charterDone, ensureCharterChecked } from './charterGate'

const HEALTH_INTERVAL_MS = 10_000

/** 首帧先当作正常,别一进页面就闪一下黄条 */
const backendOk = ref(true)
const backendHint = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
/** 卸载后别再排下一次探测,避免遗留定时器 */
let stopped = false

/**
 * 章程是否已填 —— 决定顶部导航显示完整导航还是「只需完成章程」。
 * 未填时其他入口全隐藏:路由守卫也拦,这里是让用户一眼知道现在只有这一步。
 */
const charterReady = ref(charterDone())

const route = useRoute()
// 保存章程后向导会 push 到 /import,切路由时同步导航显示(闸门内部状态已更新)
watch(
  () => route.name,
  () => {
    charterReady.value = charterDone()
  },
)

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
  // 首次挂载补一次(路由守卫通常已查过,这里是兜底 + 同步导航显示)
  void ensureCharterChecked().then((done) => {
    charterReady.value = done
  })
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
        <RouterLink v-if="charterReady" class="brand" :to="{ name: 'import' }">baleen</RouterLink>
        <span v-else class="brand">baleen · 开始使用</span>
        <nav class="nav">
          <template v-if="charterReady">
            <RouterLink class="nav-link" :to="{ name: 'import' }" active-class="nav-link--active">
              导入
            </RouterLink>
            <RouterLink class="nav-link" :to="{ name: 'notes' }" active-class="nav-link--active">
              笔记库
            </RouterLink>
            <RouterLink class="nav-link" :to="{ name: 'search' }" active-class="nav-link--active">
              检索
            </RouterLink>
            <RouterLink class="nav-link" :to="{ name: 'qa' }" active-class="nav-link--active">
              问答
            </RouterLink>
            <RouterLink class="nav-link" :to="{ name: 'settings' }" active-class="nav-link--active">
              设置
            </RouterLink>
          </template>
          <RouterLink
            v-else
            class="nav-link"
            :to="{ name: 'charter' }"
            active-class="nav-link--active"
          >
            知识库章程
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
      <RouterView v-slot="{ Component }">
        <keep-alive :exclude="['CharterWizard']">
          <component :is="Component" />
        </keep-alive>
      </RouterView>
    </main>
  </div>
</template>
