/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['"Geist Variable"', 'system-ui', '-apple-system', 'sans-serif'],
        mono: ['"Geist Mono Variable"', 'ui-monospace', 'SFMono-Regular', 'monospace'],
      },
      colors: {
        paper: '#FAF7F2',
        ink: '#1A1612',
        line: '#E8E2D7',
        muted: '#8A8278',
        accent: {
          DEFAULT: '#C75D44',
          soft: '#F0DAD2',
        },
      },
      keyframes: {
        'fade-in-up': {
          '0%': { opacity: '0', transform: 'translateY(4px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        // A data packet travelling down the connector between two nodes.
        'flow-dot': {
          '0%': { transform: 'translateY(-8px)', opacity: '0' },
          '20%': { opacity: '1' },
          '80%': { opacity: '1' },
          '100%': { transform: 'translateY(26px)', opacity: '0' },
        },
        // Live throughput bars while a stage is active.
        'bar-live': {
          '0%, 100%': { transform: 'scaleY(0.3)' },
          '50%': { transform: 'scaleY(1)' },
        },
        // Skeleton shimmer for unknown counts mid-run.
        shimmer: {
          '0%': { backgroundPosition: '-200% 0' },
          '100%': { backgroundPosition: '200% 0' },
        },
        // The "show data flow" window pulling down from the top.
        'slide-down': {
          '0%': { transform: 'translateY(-100%)' },
          '100%': { transform: 'translateY(0)' },
        },
        'fade-in': {
          '0%': { opacity: '0' },
          '100%': { opacity: '1' },
        },
        // Funnel bar growing in from the left.
        'grow-x': {
          '0%': { transform: 'scaleX(0)' },
          '100%': { transform: 'scaleX(1)' },
        },
      },
      animation: {
        'fade-in-up': 'fade-in-up 220ms ease-out both',
        'flow-dot': 'flow-dot 1.1s linear infinite',
        'bar-live': 'bar-live 900ms ease-in-out infinite',
        shimmer: 'shimmer 1.4s linear infinite',
        'slide-down': 'slide-down 300ms cubic-bezier(0.22, 1, 0.36, 1) both',
        'fade-in': 'fade-in 200ms ease-out both',
        'grow-x': 'grow-x 500ms cubic-bezier(0.22, 1, 0.36, 1) both',
      },
    },
  },
  plugins: [],
};
