import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const backendPort = process.env.VITE_API_PORT || '8000'
const httpTarget = `http://localhost:${backendPort}`
const wsTarget = `ws://localhost:${backendPort}`

export default defineConfig({
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
})
