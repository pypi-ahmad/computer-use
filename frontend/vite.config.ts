/// <reference types="vitest/config" />
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, '.', '')
  const port = env.VITE_API_PORT || '8100'
  const frontendPort = Number(env.VITE_PORT || '8505')
  return { plugins: [react()], server: { port: frontendPort, proxy: { '/api': { target: `http://127.0.0.1:${port}` }, '/api/v2/ws': { target: `ws://127.0.0.1:${port}`, ws: true } } }, test: { globals: true, environment: 'jsdom', setupFiles: ['./src/test/setup.ts'], css: true, restoreMocks: true, clearMocks: true, include: ['src/**/*.{test,spec}.{ts,tsx}'] } }
})
