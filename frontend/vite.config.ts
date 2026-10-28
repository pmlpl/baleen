import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// dev 期把请求代理到本地后端(uv run fastapi dev 默认 127.0.0.1:8000)
//
// ⚠️ 这里**绝不能** rewrite 掉 /api 前缀:
// 后端路由本身就带前缀(/api/sources、/api/notes…),把前缀剥掉会让
// /api/sources 变成 /sources → 全部 404。见 docs/api.md 开头的约定。
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      // /healthz 是唯一不带前缀的端点(docs/api.md §1「历史原因,别改」),单独代理一条
      '/healthz': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
})
