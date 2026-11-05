import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Keep the existing REACT_APP_* names so docker-compose and .env files do not change.
  envPrefix: ['VITE_', 'REACT_APP_'],
  server: { host: true, port: 3000, strictPort: true, watch: { usePolling: true } },
  build: { outDir: 'build' },
  test: { environment: 'jsdom', globals: true, setupFiles: './src/setupTests.ts', css: false },
});
