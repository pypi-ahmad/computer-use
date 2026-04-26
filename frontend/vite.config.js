import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// Use loadEnv so VITE_API_PORT in .env / .env.local is picked up by the
// config file itself (process.env doesn't auto-include Vite env files
// during config evaluation — only import.meta.env does, on the client).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const backendPort = env.VITE_API_PORT || process.env.VITE_API_PORT || '8100'
  const httpTarget = `http://localhost:${backendPort}`
  const wsTarget = `ws://localhost:${backendPort}`

  return {
  plugins: [react()],
  server: {
    port: 3000,
    proxy: {
      '/api': {
        target: httpTarget,
        changeOrigin: true,
      },
      '/ws': {
        target: wsTarget,
        ws: true,
      },
      '/vnc': {
        target: httpTarget,
        changeOrigin: true,
        ws: true,
      },
      '/docs': {
        target: httpTarget,
        changeOrigin: true,
      },
      '/redoc': {
        target: httpTarget,
        changeOrigin: true,
      },
      '/openapi.json': {
        target: httpTarget,
        changeOrigin: true,
      },
    },
  },
  }
})
