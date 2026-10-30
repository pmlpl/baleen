import { createRouter, createWebHistory } from 'vue-router'

import { ensureCharterChecked } from '../charterGate'

// 五页界面故意用静态 import,不做路由懒加载:
// 1) 总共就 5 个视图,体积很小,拆 chunk 省不下多少;
// 2) 答辩演示时不想因为分块请求多一次网络往返(或断网)出现白屏;
// 3) 静态 import 让 vue-tsc 一次性把全部视图都查一遍,漏写类型跑不过 type-check。
import CharterWizard from '../views/CharterWizard.vue'
import ImportView from '../views/ImportView.vue'
import LibraryView from '../views/LibraryView.vue'
import NoteView from '../views/NoteView.vue'
import QaView from '../views/QaView.vue'
import SearchView from '../views/SearchView.vue'
import SettingsView from '../views/SettingsView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: { name: 'import' } },
    { path: '/import', name: 'import', component: ImportView },
    { path: '/notes', name: 'notes', component: LibraryView },
    { path: '/notes/:id', name: 'note', component: NoteView, props: true },
    { path: '/qa', name: 'qa', component: QaView },
    // 首次强制填(无「稍后再填」);填过后只能从设置页进,是编辑模式
    { path: '/charter', name: 'charter', component: CharterWizard },
    { path: '/search', name: 'search', component: SearchView },
    { path: '/settings', name: 'settings', component: SettingsView },
    // 兜底:路径写错也回导入页,别让人看到空白
    { path: '/:pathMatch(.*)*', redirect: { name: 'import' } },
  ],
})

/**
 * 章程闸门:没填过问卷的话,除 /charter 外一概拦回 /charter。
 * 单用户无登录,「首次登录」= 首次访问且 DB 无 charter。
 * 检查只做一次(结果缓存在 charterGate),后端不可用时放行不锁死。
 */
router.beforeEach(async (to) => {
  const done = await ensureCharterChecked()
  if (!done && to.name !== 'charter') {
    return { name: 'charter' }
  }
  return true
})

export default router
