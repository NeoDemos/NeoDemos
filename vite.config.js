import { defineConfig } from 'vite';
import tailwindcss from '@tailwindcss/vite';

export default defineConfig({
  plugins: [tailwindcss()],
  base: '/static/dist/',
  build: {
    outDir: 'static/dist',
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: 'static/css/main.css',
        app: 'static/js/app.js',
      },
      output: {
        assetFileNames: '[name].[ext]',
        entryFileNames: '[name].js',
      },
    },
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
      '/': 'http://localhost:8000',
    },
  },
});
