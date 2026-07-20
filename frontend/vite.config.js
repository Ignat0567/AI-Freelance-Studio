import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { readFileSync } from 'node:fs';

const packageMetadata = JSON.parse(readFileSync(new URL('./package.json', import.meta.url), 'utf8'));

// https://vitejs.dev
export default defineConfig({
    plugins: [react()],
    define: {
        'import.meta.env.VITE_APP_VERSION': JSON.stringify(packageMetadata.version),
    },
    base: './', // Ensures assets are loaded correctly via file:// protocol in Electron
    server: {
        port: parseInt(process.env.VITE_PORT || '3000', 10)
    }
});
