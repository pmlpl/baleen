import { createRouter, createWebHistory } from 'vue-router'

import { ensureGateChecked } from '../onboardingGate'
import { useAuthStore } from '../stores/auth'

// 页面有意精简(问答为主界面,导入/检索已并入会话)后仍用静态 import,不做路由懒加载:
// 1) 总共就 5 个视图,体积很小,拆 chunk 省不下多少;
// 2) 答辩演示时不想因为分块请求多一次网络往返(或断网)出现白屏;
// 3) 静态 import 让 vue-tsc 一次性把全部视图都查一遍,漏写类型跑不过 type-check。
import CharterWizard from '../views/CharterWizard.vue'
import LibraryView from '../views/LibraryView.vue'
import LoginView from '../views/LoginView.vue'
import NoteView from '../views/NoteView.vue'
import QaView from '../views/QaView.vue'
import SettingsView from '../views/SettingsView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: { name: 'qa' } },
    { path: '/login', name: 'login', component: LoginView },
    { path: '/notes', name: 'notes', component: LibraryView },
    { path: '/notes/:id', name: 'note', component: NoteView, props: true },
    { path: '/qa', name: 'qa', component: QaView },
    // 首次强制填(无「稍后再填」);填过后只能从设置页进,是编辑模式
    { path: '/charter', name: 'charter', component: CharterWizard },
    { path: '/settings', name: 'settings', component: SettingsView },
    // 兜底:路径写错也回导入页,别让人看到空白
    { path: '/:pathMatch(.*)*', redirect: { name: 'qa' } },
  ],
})

/**
 * 引导闸门,顺序固定:登录 → 配 API Key → 填章程 → 正常使用。
 *
 * 用 auth store 的 restored 标志等启动恢复完成再判断,否则刷新页面会被误判未登录。
 * 后端不可用时 ensureGateChecked 返回「都已完成」,不把用户锁在门外。
 */
router.beforeEach(async (to) => {
  const auth = useAuthStore()
  if (!auth.restored) await auth.restore()

  // ① 未登录:只能待在 /login
  if (!auth.isAuthenticated) {
    return to.name === 'login' ? true : { name: 'login' }
  }
  // 已登录却访问 /login → 回首页(继续后面的引导)
  if (to.name === 'login') return { name: 'qa' }

  // ② 已登录但还没配 API Key:只能去设置页(或章程页,但先配 key)
  const gate = await ensureGateChecked()
  if (!gate.hasKey) {
    return to.name === 'settings' ? true : { name: 'settings' }
  }

  // ③ 配了 key 但没填章程:只能去章程页
  if (!gate.hasCharter) {
    return to.name === 'charter' ? true : { name: 'charter' }
  }

  return true
})

export default router
