<script setup lang="ts">
/**
 * 应用外壳:顶部导航(三页) + 后端健康状态 + 路由出口。
 * 健康检查走 GET /healthz(docs/api.md §1);status != ok 就给黄条,不是 ok 也不挡页面。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { getHealth } from './api/health'
import { describeApiError } from './api/http'
import { ensureGateChecked, gateState, onboardingDone, resetGate } from './onboardingGate'
import { useAuthStore } from './stores/auth'

const HEALTH_INTERVAL_MS = 10_000

/** 首帧先当作正常,别一进页面就闪一下黄条 */
const backendOk = ref(true)
const backendHint = ref('')
let timer: ReturnType<typeof setTimeout> | undefined
/** 卸载后别再排下一次探测,避免遗留定时器 */
let stopped = false

const auth = useAuthStore()
const router = useRouter()

/** 登录 + 配 key + 填章程 都完成 → 显示完整导航;否则只显示当前该做的那一步 */
const ready = ref(onboardingDone())
/** 是否已配 API Key(决定顶部只显示「配置 API Key」还是「知识库章程」) */
const gateHasKey = ref(gateState().hasKey)

const route = useRoute()
// 每个阶段完成后都会 push 到下个路由,借路由变化同步导航显示
watch(
  () => route.name,
  async () => {
    await ensureGateChecked()
    ready.value = onboardingDone()
    gateHasKey.value = gateState().hasKey
  },
)

function logout(): void {
  auth.logout()
  resetGate()
  void router.push({ name: 'login' })
}

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
  void ensureGateChecked().then(() => {
    ready.value = onboardingDone()
    gateHasKey.value = gateState().hasKey
  })
})

onBeforeUnmount(() => {
  stopped = true
  if (timer !== undefined) clearTimeout(timer)
})
</script>
<template>
  <div class="app-shell">
    <!-- 未登录不显示导航 -->
    <header v-if="auth.isAuthenticated" class="topbar">
      <div class="topbar-inner">
        <RouterLink v-if="ready" class="brand" :to="{ name: 'import' }">baleen</RouterLink>
        <span v-else class="brand">baleen · 开始使用</span>
        <nav class="nav">
          <template v-if="ready">
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
          <!-- 还没配 key:只能去设置页 -->
          <RouterLink
            v-else-if="!gateHasKey"
            class="nav-link"
            :to="{ name: 'settings' }"
            active-class="nav-link--active"
          >
            配置 API Key
          </RouterLink>
          <!-- 配了 key 但没填章程:只能去章程页 -->
          <RouterLink
            v-else
            class="nav-link"
            :to="{ name: 'charter' }"
            active-class="nav-link--active"
          >
            知识库章程
          </RouterLink>
        </nav>
        <span class="user-box">
          <span class="user-name">{{ auth.username }}</span>
          <a class="logout" @click="logout">退出</a>
        </span>
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
        <keep-alive :exclude="['CharterWizard', 'LoginView']">
          <component :is="Component" />
        </keep-alive>
      </RouterView>
    </main>
  </div>
</template>

<style scoped>
.user-box {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-right: 14px;
  font-size: 13px;
}

.user-name {
  color: var(--ink-2);
}

.logout {
  color: var(--ink-3);
  cursor: pointer;
}

.logout:hover {
  color: var(--accent);
}
</style>
