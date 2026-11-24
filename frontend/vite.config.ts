import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig({
  plugins: [vue()],
  server: {
    host: '127.0.0.1',
    port: 5173,
    // 项目在 OneDrive 目录里，原生文件监听偶尔漏事件，HMR 会继续用旧模块。
    // 轮询稍慢，但能保证改完代码刷新就是新逻辑。
    watch: {
      usePolling: true,
      interval: 800,
    },
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8010',
        changeOrigin: true,
      },
    },
  },
})