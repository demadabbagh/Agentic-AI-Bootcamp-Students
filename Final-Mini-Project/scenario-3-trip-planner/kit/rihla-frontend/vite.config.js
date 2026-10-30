import process from 'node:process';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      '/api': {
        // The backend to proxy to; RIHLA_BACKEND_URL lets a second backend be tried without editing this file.
        target: process.env.RIHLA_BACKEND_URL || 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
    },
  },
});
