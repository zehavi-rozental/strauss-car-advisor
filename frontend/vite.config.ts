import react from '@vitejs/plugin-react'
import { configDefaults, defineConfig } from 'vitest/config'

// The client calls relative /api/... only. In development Vite forwards them to the backend;
// in deployment the client is served from the same origin as the API.
const apiTarget = process.env.VITE_API_PROXY_TARGET ?? 'http://localhost:8000'
const api = { '/api': { target: apiTarget, changeOrigin: true } }

export default defineConfig({
  plugins: [react()],
  server: { proxy: api },
  preview: { proxy: api },
  // e2e/ holds Playwright specs (run with `npx playwright test`); vitest must not load them
  test: { exclude: [...configDefaults.exclude, 'e2e/**'] },
})
