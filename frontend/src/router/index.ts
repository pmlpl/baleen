import { createRouter, createWebHistory } from 'vue-router'

// 三页界面故意用静态 import,不做路由懒加载:
// 1) 总共就 3 个视图,体积很小,拆 chunk 省不下多少;
// 2) 答辩演示时不想因为分块请求多一次网络往返(或断网)出现白屏;
// 3) 静态 import 让 vue-tsc 一次性把全部视图都查一遍,漏写类型跑不过 type-check。
import ImportView from '../views/ImportView.vue'
import LibraryView from '../views/LibraryView.vue'
import NoteView from '../views/NoteView.vue'
import SearchView from '../views/SearchView.vue'

const router = createRouter({
  history: createWebHistory(import.meta.env.BASE_URL),
  routes: [
    { path: '/', redirect: { name: 'import' } },
    { path: '/import', name: 'import', component: ImportView },
    { path: '/notes', name: 'notes', component: LibraryView },
    { path: '/notes/:id', name: 'note', component: NoteView, props: true },
    { path: '/search', name: 'search', component: SearchView },
    // 兜底:路径写错也回导入页,别让人看到空白
    { path: '/:pathMatch(.*)*', redirect: { name: 'import' } },
  ],
})

export default router
