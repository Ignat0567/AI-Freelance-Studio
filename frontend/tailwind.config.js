import defaultTheme from 'tailwindcss/defaultTheme.js';

/** @type {import('tailwindcss').Config} */
export default {
    content: [
        "./index.html",
        "./src/**/*.{js,ts,jsx,tsx}",
    ],
    theme: {
        colors: {
            ...defaultTheme.colors,
            studio: {
                bg: '#0f172a',
                panel: '#1e293b',
                accent: '#38bdf8',
            },
            slate: {
                50: 'rgb(var(--slate-50) / <alpha-value>)',
                100: 'rgb(var(--slate-100) / <alpha-value>)',
                200: 'rgb(var(--slate-200) / <alpha-value>)',
                300: 'rgb(var(--slate-300) / <alpha-value>)',
                400: 'rgb(var(--slate-400) / <alpha-value>)',
                500: 'rgb(var(--slate-500) / <alpha-value>)',
                600: 'rgb(var(--slate-600) / <alpha-value>)',
                700: 'rgb(var(--slate-700) / <alpha-value>)',
                800: 'rgb(var(--slate-800) / <alpha-value>)',
                900: 'rgb(var(--slate-900) / <alpha-value>)',
                950: 'rgb(var(--slate-950) / <alpha-value>)',
            },
        },
        extend: {},
    },
    plugins: [],
}
