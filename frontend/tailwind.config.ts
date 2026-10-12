import type { Config } from 'tailwindcss';
import { ui } from './src/theme/tokens';

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: ui.bg,
        panel: ui.panel,
        panel2: ui.panel2,
        line: ui.border,
        ink: ui.text,
        muted: ui.muted,
        faint: ui.faint,
        pass: ui.pass,
        hold: ui.hold,
        fail: ui.fail,
        info: ui.info,
        armed: ui.armed,
        stale: ui.stale,
        pred: ui.info,
      },
      fontFamily: {
        display: ['Inter', 'system-ui', 'sans-serif'],
        sans: ['Inter', 'system-ui', 'sans-serif'],
        mono: ['"JetBrains Mono"', 'ui-monospace', 'monospace'],
      },
      borderRadius: {
        panel: '4px',
      },
      backdropBlur: {
        panel: '8px',
      },
      transitionTimingFunction: {
        out: 'cubic-bezier(0.16, 1, 0.3, 1)',
      },
      keyframes: {
        'status-pulse': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.45' },
        },
        'abort-flash': {
          '0%, 100%': { opacity: '1' },
          '50%': { opacity: '0.35' },
        },
      },
      animation: {
        'status-pulse': 'status-pulse 2.4s ease-in-out infinite',
        'abort-flash': 'abort-flash 0.6s ease-out 3',
      },
    },
  },
  plugins: [],
} satisfies Config;
