import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiTarget = env.MEDCONNECT_API_URL || 'http://127.0.0.1:8000'

  return {
    plugins: [vue()],
    server: {
      host: '127.0.0.1',
      proxy: {
        '/api': apiTarget,
        '/health': apiTarget,
      },
    },
    preview: {
      host: '127.0.0.1',
      proxy: {
        '/api': apiTarget,
        '/health': apiTarget,
      },
    },
  }
})
