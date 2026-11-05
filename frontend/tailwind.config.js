/** @type {import('tailwindcss').Config} */
export default {
  darkMode: ['class'],
  content: ['./index.html', './src/**/*.{js,jsx,ts,tsx}'],
  theme: {
    extend: {
      colors: {
        // VoiceVault design tokens
        // Theme variables (data/themes.js); "R G B" so opacity modifiers work.
        vault: {
          bg:        'rgb(var(--vv-bg) / <alpha-value>)',
          surface:   'rgb(var(--vv-surface) / <alpha-value>)',
          elevated:  'rgb(var(--vv-elevated) / <alpha-value>)',
          border:    'rgb(var(--vv-border) / <alpha-value>)',
          accent:    'rgb(var(--vv-accent) / <alpha-value>)',
          'accent-2':'rgb(var(--vv-accent2) / <alpha-value>)',
          glow:      'rgb(var(--vv-glow) / <alpha-value>)',
          text:      'rgb(var(--vv-text) / <alpha-value>)',
          muted:     'rgb(var(--vv-muted) / <alpha-value>)',
          'on-accent': 'rgb(var(--vv-on-accent) / <alpha-value>)',
        }
      },
      fontFamily: {
        display: ['"Syne"', 'sans-serif'],
        body:    ['"DM Sans"', 'sans-serif'],
        mono:    ['"JetBrains Mono"', 'monospace'],
      },
      backgroundImage: {
        'vault-gradient': 'var(--vv-bg-gradient)',
        'accent-gradient': 'var(--vv-accent-gradient)',
      },
      boxShadow: {
        'glow-sm':  '0 0 12px rgb(var(--vv-accent) / 0.3)',
        'glow-md':  '0 0 24px rgb(var(--vv-accent) / 0.4)',
        'glow-lg':  '0 0 48px rgb(var(--vv-accent) / 0.5)',
      },
      animation: {
        'pulse-slow': 'pulse 3s cubic-bezier(0.4, 0, 0.6, 1) infinite',
        'fade-in':    'fadeIn 0.4s ease forwards',
        'slide-up':   'slideUp 0.4s ease forwards',
      },
      keyframes: {
        fadeIn:  { from: { opacity: 0 }, to: { opacity: 1 } },
        slideUp: { from: { opacity: 0, transform: 'translateY(16px)' }, to: { opacity: 1, transform: 'translateY(0)' } },
      }
    },
  },
  plugins: [],
}
