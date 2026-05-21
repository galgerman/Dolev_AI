/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        buy: '#22c55e',
        sell: '#ef4444',
        near: '#f97316',
        warn: '#eab308',
      },
      animation: {
        pulse_fast: 'pulse 0.8s cubic-bezier(0.4,0,0.6,1) 3',
      },
    },
  },
  plugins: [],
}
