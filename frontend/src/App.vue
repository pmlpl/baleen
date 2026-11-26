<script setup lang="ts">
/**
 * 应用外壳,三种形态:
 * 1. 未登录 → 只渲染登录页自身;
 * 2. 引导期(配 API Key / 填章程)→ 保留单入口顶栏,不许乱逛;
 * 3. 引导完成 → 「统一工作台」:极简顶栏(品牌+用户)+ 左侧书立导航 + 内容舞台。
 *    舞台用 keep-alive 承载各视图 —— 从问答点引用跳到笔记再回来,
 *    对话消息、滚动位置、输入草稿都还在,不需要任何「恢复」逻辑。
 */
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { RouterLink, RouterView, useRoute, useRouter } from 'vue-router'

import { describeApiError } from './api/http'
import { deleteNote } from './api/notes'
import BasketModal from './components/BasketModal.vue'
import ConfirmDialog from './components/ConfirmDialog.vue'
import { ensureGateChecked, gateState, onboardingDone, resetGate } from './onboardingGate'
import { useAuthStore } from './stores/auth'
import { useTrashStore } from './stores/trash'
import { flyToBasket } from './utils/flyPaper'

const auth = useAuthStore()
const router = useRouter()
const trash = useTrashStore()

/** 登录 + 配 key + 填章程 都完成 → 进工作台;否则只显示当前该做的那一步 */
const ready = ref(onboardingDone())
/** 是否已配 API Key(决定引导期只显示「配置 API Key」还是「知识库章程」) */
const gateHasKey = ref(gateState().hasKey)

const route = useRoute()
/** 书架/视频是浏览型大页面,舞台放宽;问答走「出血」形态(整页就是一张纸,不露桌面);
 *  笔记阅读/设置/个人中心保持 1060px 书页宽 */
const isWide = computed(() => route.name === 'notes' || route.name === 'video')
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

/** ---- 纸篓(书立左下):桌面回收站隐喻 ---- */

/** 点纸篓 → 弹出纸篓弹窗(不切页面,页面中央悬浮窗 + 朦胧遮罩) */
const basketOpen = ref(false)
/** 拖拽悬停在篓口:朝人倾过来 */
const basketOver = ref(false)
/** 落纸后弹一下(catch 动画) */
const basketPulse = ref(false)
/** 拖放移入失败时的提示(卡片已飞走但后端没记账,必须让人知道) */
const basketError = ref('')

function pulseBasket(): void {
  basketPulse.value = false
  // 先摘掉类再挂回去,连续落纸时动画才能重新触发
  void nextTick(() => {
    basketPulse.value = true
  })
  window.setTimeout(() => {
    basketPulse.value = false
  }, 620)
}

/**
 * 书卡拖到篓口松手:**移入纸篓**(软删除 API),不是真删。
 *
 * 顺序是有讲究的:先飞纸(pure 视觉)→ 再调 API → 成功了才刷角标。
 * 飞纸与请求并行会让「动画飞完了但服务端失败」变得不可见,所以等动画结束再发请求,
 * 失败时用错误提示告知 —— 卡片已经在书架被拖走了,但后端没记账,刷新会回来。
 */
async function onBasketDrop(event: DragEvent): Promise<void> {
  event.preventDefault()
  basketOver.value = false
  const raw = event.dataTransfer?.getData('application/x-baleen-note') ?? ''
  if (raw === '') return
  let payload: { id?: unknown; spine?: unknown }
  try {
    payload = JSON.parse(raw) as typeof payload
  } catch {
    return // 坏数据忽略
  }
  const id = Number(payload.id)
  if (!Number.isFinite(id) || id <= 0) return
  trash.startFly(id) // 书架上的卡原地淡出
  pulseBasket()
  // 源=松手点(书跟着光标一路拖过来,从光标处继续落篓最顺)
  await flyToBasket(
    { left: event.clientX - 14, top: event.clientY - 18, width: 28, height: 36 },
    typeof payload.spine === 'string' ? payload.spine : undefined,
  )
  try {
    await deleteNote(id)
    await trash.refreshCount()
  } catch (error) {
    basketError.value = `移入纸篓失败:${describeApiError(error)}`
  } finally {
    trash.endFly(id)
  }
}

onMounted(() => {
  // 首次挂载补一次(路由守卫通常已查过,这里是兜底 + 同步导航显示)
  void ensureGateChecked().then(() => {
    ready.value = onboardingDone()
    gateHasKey.value = gateState().hasKey
    // 纸篓角标:后端账号级数据,打开应用先同步一次(v37)
    if (auth.isAuthenticated) void trash.refreshCount()
  })
})

// 切页时同步纸篓数量:在纸篓弹窗里放回/彻底删之后,角标要跟着变
watch(
  () => route.name,
  () => {
    if (auth.isAuthenticated && ready.value) void trash.refreshCount()
  },
)

// 关掉弹窗时也刷一次(弹窗内可能放回/彻底删过)
watch(basketOpen, (open) => {
  if (!open && auth.isAuthenticated) void trash.refreshCount()
})
</script>
<template>
  <!-- ① 未登录:登录页自带布局,壳什么都不渲染 -->
  <div v-if="!auth.isAuthenticated" class="app-shell">
    <RouterView />
  </div>

  <!-- ② 引导期:单入口顶栏(没配 key 只能去设置,配了没填章程只能去章程) -->
  <div v-else-if="!ready" class="app-shell">
    <header class="topbar">
      <div class="topbar-inner">
        <span class="brand">baleen · 开始使用</span>
        <nav class="nav">
          <RouterLink
            v-if="!gateHasKey"
            class="nav-link"
            :to="{ name: 'settings' }"
            active-class="nav-link--active"
          >
            配置 API Key
          </RouterLink>
          <RouterLink v-else class="nav-link" :to="{ name: 'charter' }" active-class="nav-link--active">
            知识库章程
          </RouterLink>
        </nav>
        <span class="user-box">
          <span class="user-name">{{ auth.username }}</span>
          <a class="logout" @click="logout">退出</a>
        </span>
      </div>
    </header>
    <main class="container">
      <RouterView />
    </main>
  </div>

  <!-- ③ 统一工作台:左书立 + 内容舞台 -->
  <div v-else class="app-shell app-shell--wb">
    <header class="topbar">
      <div class="topbar-inner topbar-inner--wb">
        <RouterLink class="brand" :to="{ name: 'qa' }">baleen</RouterLink>
        <span class="user-box user-box--wb">
          <RouterLink class="nav-link" :to="{ name: 'charter' }" active-class="nav-link--active">
            知识库章程
          </RouterLink>
          <span class="user-name">{{ auth.username }}</span>
          <a class="logout" @click="logout">退出</a>
        </span>
      </div>
    </header>

    <div class="wb-body">
      <aside class="rail" aria-label="工作台导航">
        <nav class="rail-nav">
          <RouterLink
            class="rail-item"
            :to="{ name: 'notes' }"
            active-class="rail-item--active"
            title="笔记库"
          >
            <svg
              class="rail-ico"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20" />
              <path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z" />
            </svg>
            <span class="rail-label">书架</span>
          </RouterLink>
          <RouterLink
            class="rail-item"
            :to="{ name: 'qa' }"
            active-class="rail-item--active"
            title="问答"
          >
            <svg
              class="rail-ico"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <path d="M21 11.5a8.38 8.38 0 0 1-.9 3.8 8.5 8.5 0 0 1-7.6 4.7 8.38 8.38 0 0 1-3.8-.9L3 21l1.9-5.7a8.38 8.38 0 0 1-.9-3.8 8.5 8.5 0 0 1 4.7-7.6 8.38 8.38 0 0 1 3.8-.9h.5a8.48 8.48 0 0 1 8 8v.5z" />
            </svg>
            <span class="rail-label">问答</span>
          </RouterLink>
          <RouterLink
            class="rail-item"
            :to="{ name: 'video' }"
            active-class="rail-item--active"
            title="视频笔记"
          >
            <svg
              class="rail-ico"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <polygon points="23 7 16 12 23 17 23 7" />
              <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
            </svg>
            <span class="rail-label">视频</span>
          </RouterLink>
          <RouterLink
            class="rail-item"
            :to="{ name: 'settings' }"
            active-class="rail-item--active"
            title="设置"
          >
            <svg
              class="rail-ico"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              stroke-width="1.8"
              stroke-linecap="round"
              stroke-linejoin="round"
              aria-hidden="true"
            >
              <line x1="4" y1="21" x2="4" y2="14" />
              <line x1="4" y1="10" x2="4" y2="3" />
              <line x1="12" y1="21" x2="12" y2="12" />
              <line x1="12" y1="8" x2="12" y2="3" />
              <line x1="20" y1="21" x2="20" y2="16" />
              <line x1="20" y1="12" x2="20" y2="3" />
              <line x1="1" y1="14" x2="7" y2="14" />
              <line x1="9" y1="8" x2="15" y2="8" />
              <line x1="17" y1="16" x2="23" y2="16" />
            </svg>
            <span class="rail-label">设置</span>
          </RouterLink>
        </nav>
        <div class="rail-foot">
          <!-- 纸篓:书立里「我的」上方,苹果风毛玻璃小瓷片。书卡可拖进来(移入纸篓,可放回),点击弹纸篓窗 -->
          <button
            id="trash-basket"
            class="trash-basket"
            :class="{ 'trash-basket--over': basketOver, 'trash-basket--pulse': basketPulse }"
            type="button"
            title="纸篓"
            aria-label="纸篓"
            @click="basketOpen = true"
            @dragover.prevent="basketOver = true"
            @dragleave="basketOver = false"
            @drop.prevent="onBasketDrop"
          >
            <svg class="trash-basket__ico" viewBox="0 0 64 68" fill="none" aria-hidden="true">
              <defs>
                <linearGradient id="basket-body" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stop-color="#f7f2e5" />
                  <stop offset="1" stop-color="#e0d8c6" />
                </linearGradient>
                <linearGradient id="basket-hole" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="0" stop-color="#6b6455" />
                  <stop offset="1" stop-color="#453f38" />
                </linearGradient>
              </defs>
              <!-- 地面影:篓子立得住 -->
              <ellipse cx="32" cy="62" rx="15" ry="2.6" fill="rgba(45, 40, 32, 0.16)" />
              <!-- 篓口内洞(先画洞) -->
              <ellipse
                cx="32"
                cy="21.5"
                rx="15"
                ry="5"
                fill="url(#basket-hole)"
                stroke="#b9b19d"
                stroke-width="1.2"
              />
              <!-- 篓口冒出的纸头:篓里有书才冒出来 -->
              <g v-if="trash.count > 0">
                <polygon
                  points="25,12 32,7 39,11 37.5,22.5 26.5,23.5"
                  fill="#fffdf8"
                  stroke="#c9c2b0"
                  stroke-width="1"
                />
                <polyline points="28,13 33,9.5 36.5,13" stroke="#d8d2c2" stroke-width="1" />
              </g>
              <!-- 篓身:布面梯形,下缘收口 -->
              <path
                d="M17 22 L21.5 55 Q22 58 25 58 L39 58 Q42 58 42.5 55 L47 22 Z"
                fill="url(#basket-body)"
                stroke="#b9b19d"
                stroke-width="1.2"
              />
              <!-- 竹篾:三根竖条 + 两道箍 -->
              <path d="M23.5 24 L25.5 55.5" stroke="#cfc7b2" stroke-width="1.1" />
              <path d="M32 24 L32 56" stroke="#cfc7b2" stroke-width="1.1" />
              <path d="M40.5 24 L38.5 55.5" stroke="#cfc7b2" stroke-width="1.1" />
              <path d="M18.7 33 L45.3 33" stroke="#cfc7b2" stroke-width="1.1" />
              <path d="M20.1 44 L43.9 44" stroke="#cfc7b2" stroke-width="1.1" />
              <!-- 篓口前沿:浅色高光弧,立体感的关键 -->
              <path
                d="M17 21.5 A15 5 0 0 0 47 21.5"
                stroke="#f7f2e5"
                stroke-width="2.6"
                stroke-linecap="round"
              />
            </svg>
            <span v-if="trash.count > 0" class="trash-basket__badge">{{
              trash.count
            }}</span>
          </button>
          <!-- 拖放移入失败的提示:卡片已经飞走了但后端没记账,必须让人看见 -->
          <p v-if="basketError !== ''" class="basket-error" role="alert">
            <span>{{ basketError }}</span>
            <button type="button" aria-label="关闭提示" @click="basketError = ''">×</button>
          </p>
        </div>
      </aside>

      <main
        class="stage"
        :class="{ 'stage--bleed': route.name === 'qa', 'stage--clamp': route.name === 'note' }"
      >
        <div
          class="stage-inner"
          :class="{
            'stage-inner--wide': isWide,
            'stage-inner--bleed': route.name === 'qa',
            'stage-inner--clamp': route.name === 'note',
          }"
        >
          <RouterView v-slot="{ Component }">
            <keep-alive :exclude="['CharterWizard', 'LoginView']">
              <component :is="Component" />
            </keep-alive>
          </RouterView>
        </div>
      </main>

      <!-- 纸篓弹窗:页面中央悬浮窗,点朦胧遮罩收回 -->
      <BasketModal v-if="basketOpen" @close="basketOpen = false" />
    </div>
  </div>

  <!-- 全局确认框:整个应用只挂一次,任何页面用 confirmDialog() 唤起 -->
  <ConfirmDialog />
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

/* ---------- 统一工作台 ---------- */

/* 顶栏瘦身为一条「书桌沿」:只有品牌与用户区,导航全部移进左书立 */
.topbar-inner--wb {
  max-width: none;
  height: 52px;
  padding: 0 20px;
}

.user-box--wb {
  margin-right: 0;
  margin-left: auto;
}

.wb-body {
  display: flex;
  flex: 1;
  align-items: stretch;
}

/* 书立:比桌面深一档的纸色竖栏,托住整排导航。
   顶栏 sticky 高 52px + 1px 分隔线,书立跟随钉在 53px 处 */
.rail {
  position: sticky;
  top: 53px;
  display: flex;
  width: 78px;
  flex-shrink: 0;
  flex-direction: column;
  height: calc(100vh - 53px);
  padding: 16px 10px 14px;
  border-right: 1px solid var(--line);
  background: linear-gradient(180deg, #eee9dd, #e9e4d6);
}

.rail-nav,
.rail-foot {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.rail-foot {
  margin-top: auto;
}

/* 导航项:图标 + 竖排小字;激活 = 一锭墨(与全站主按钮同语言) */
.rail-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 4px;
  padding: 9px 4px 7px;
  border-radius: 10px;
  color: var(--ink-2);
  font-size: 12.5px;
  text-align: center;
  transition:
    background 0.15s ease,
    color 0.15s ease;
}

.rail-item:hover {
  background: rgba(255, 253, 248, 0.72);
  color: var(--ink);
  text-decoration: none;
}

.rail-item--active,
.rail-item--active:hover {
  background: var(--ink);
  color: var(--panel);
  font-weight: 600;
}

.rail-ico {
  width: 18px;
  height: 18px;
}

.rail-label {
  line-height: 1;
}

/* 内容舞台:同一块桌面,各视图轮流上台;浏览型页面放宽到 1440px */
.stage {
  flex: 1;
  min-width: 0;
  padding: 26px 30px 56px;
}

.stage-inner {
  max-width: 1060px;
  margin: 0 auto;
}

.stage-inner--wide {
  max-width: 1440px;
}

/* 问答出血形态:问答页整页就是一张纸——撤掉桌面留白,纸面铺满视口剩余区域;
   高度钳在视口内(--chrome-h = 顶栏 52px + 1px 分隔线),消息区才能内滚、输入栏钉底 */
.stage--bleed {
  display: flex;
  height: calc(100vh - var(--chrome-h));
  min-height: 0;
  padding: 0;
}

.stage-inner--bleed {
  display: flex;
  width: 100%;
  max-width: none;
  min-height: 0;
  flex-direction: column;
}

/* 笔记阅读/编辑:同样钳在视口内,但保留舞台留白。
   两栏各自内滚靠的是「祖先有确定高度」——以前 NoteView 自己写 calc(100vh - 96px),
   顶栏 53 + 舞台上下内边距 82 一加就超了 39px,页面多出一层滚动条 */
.stage--clamp {
  display: flex;
  height: calc(100vh - var(--chrome-h));
  min-height: 0;
  flex-direction: column;
}

.stage-inner--clamp {
  display: flex;
  flex: 1;
  min-height: 0;
  flex-direction: column;
}

/* 窄屏:笔记页目录折到上方,整页改为自然高度滚动,不再钳视口 */
@media (max-width: 900px) {
  .stage--clamp {
    height: auto;
  }
}

/* ---------- 纸篓:书立里「我的」上方的一块苹果风毛玻璃瓷片 ---------- */

.trash-basket {
  position: relative;
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  margin: 0 auto 2px;
  padding: 0;
  border: 1px solid rgba(255, 255, 255, 0.6);
  border-radius: 16px;
  background: rgba(255, 255, 255, 0.38);
  /* 苹果味的核心:半透明白 + backdrop 毛玻璃 + 顶部内高光 */
  backdrop-filter: blur(8px);
  -webkit-backdrop-filter: blur(8px);
  box-shadow:
    0 4px 14px rgba(45, 40, 32, 0.12),
    inset 0 1px 0 rgba(255, 255, 255, 0.65);
  cursor: pointer;
  transition:
    transform 0.16s ease,
    background 0.16s ease,
    box-shadow 0.16s ease;
}

.trash-basket:hover {
  transform: translateY(-2px) scale(1.04);
  background: rgba(255, 255, 255, 0.55);
  box-shadow:
    0 8px 20px rgba(45, 40, 32, 0.16),
    inset 0 1px 0 rgba(255, 255, 255, 0.7);
}

.trash-basket:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 3px;
}

/* 拖拽悬停:微微放大提亮,迎接着陆 */
.trash-basket--over,
.trash-basket--over:hover {
  transform: scale(1.08);
  background: rgba(255, 255, 255, 0.62);
}

/* 落纸弹一下 */
.trash-basket--pulse {
  animation: basket-catch 0.55s ease;
}

@keyframes basket-catch {
  0% {
    transform: scale(1);
  }

  30% {
    transform: scale(1.16) rotate(-4deg);
  }

  60% {
    transform: scale(0.95) rotate(1deg);
  }

  100% {
    transform: scale(1);
  }
}

.trash-basket__ico {
  display: block;
  width: 44px;
  height: 46px;
}

/* 篓里有多少本的角标:朱砂小圆,像盖了个章 */
/* 拖放失败提示:浮在纸篓上方,不挤布局 */
.basket-error {
  position: absolute;
  bottom: 96px;
  left: 8px;
  z-index: 5;
  display: flex;
  width: 200px;
  align-items: flex-start;
  gap: 6px;
  padding: 8px 10px;
  border: 1px solid #dcb4ac;
  border-radius: 8px;
  background: var(--err-soft);
  color: var(--err);
  font-size: 12px;
  line-height: 1.5;
}

.basket-error button {
  flex-shrink: 0;
  padding: 0 2px;
  border: none;
  background: none;
  color: var(--err);
  cursor: pointer;
  font-size: 13px;
  line-height: 1;
}

.trash-basket__badge {
  position: absolute;
  top: -5px;
  right: -5px;
  min-width: 18px;
  height: 18px;
  padding: 0 5px;
  border-radius: 9px;
  background: var(--seal);
  color: #fff;
  font-size: 11px;
  line-height: 18px;
  text-align: center;
  font-variant-numeric: tabular-nums;
}
</style>
