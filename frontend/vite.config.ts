import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The client calls relative /api/... only. In development Vite forwards them to the backend;
// in deployment the client is served from the same origin as the API.
const api = { '/api': { target: 'http://localhost:8000', changeOrigin: true } }

export default defineConfig({
  plugins: [react()],
  server: { proxy: api },
  preview: { proxy: api },
})
