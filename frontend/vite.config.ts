import { defineConfig, loadEnv } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig(({ mode }) => ({
  plugins: [react()],
  // Keep the existing REACT_APP_* names so docker-compose and .env files do not change.
  envPrefix: ['VITE_', 'REACT_APP_'],
  server: {
    host: true, port: 3000, strictPort: true, watch: { usePolling: true },
    // Keep custom completion headers same-origin in the local browser demo.
    proxy: {
      '/api': {
        target: loadEnv(mode, '.', 'REACT_APP_').REACT_APP_API_URL || 'http://127.0.0.1:8000/api',
        changeOrigin: true,
        rewrite: path => path.replace(/^\/api/, ''),
      },
    },
  },
  build: { outDir: 'build' },
  test: { environment: 'jsdom', globals: true, setupFiles: './src/setupTests.ts', css: false },
}));
