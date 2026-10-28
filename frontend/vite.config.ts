import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vite'

// dev 期把 /api 代理到本地后端(uv run fastapi dev 默认 127.0.0.1:8000)
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
})
