import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react({ include: /\.(js|jsx|ts|tsx)$/ })],
  // Keep the existing REACT_APP_* names so docker-compose and .env files do not change.
  envPrefix: ['VITE_', 'REACT_APP_'],
  // Existing files contain JSX inside .js; this goes away once they are converted to .tsx.
  esbuild: { loader: 'tsx', include: /src\/.*\.[jt]sx?$/, exclude: [] },
  optimizeDeps: { esbuildOptions: { loader: { '.js': 'jsx' } } },
  server: { host: true, port: 3000, strictPort: true, watch: { usePolling: true } },
  build: { outDir: 'build' },
  test: { environment: 'jsdom', globals: true, setupFiles: './src/setupTests.js', css: false },
});
