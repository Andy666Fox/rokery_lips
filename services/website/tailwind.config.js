/** Tailwind build for the static site. Output: html/styles.css (committed, served by Caddy). */
export default {
	content: ['./html/**/*.{html,js}', '!./html/vendor/**', '!./html/pinball/**'],
	theme: {
		extend: {
			colors: {
				page: { DEFAULT: '#2c2430', glow: '#55465c' },
				paper: { DEFAULT: '#efe2cf', light: '#faf1e1', hover: '#ead6cc', deep: '#e4d3c2' },
				ink: '#40343b',
				muted: '#635059',
				accent: { DEFAULT: '#965f6d', dark: '#754b5a' },
				shade: '#140e16',
			},
			fontFamily: {
				sans: ['Inter', 'system-ui', '-apple-system', 'sans-serif'],
				display: ['"Space Grotesk"', 'Inter', 'sans-serif'],
				dragonic: ['Dragonic', 'serif'],
			},
			screens: {
				sm: '600px',
				md: '960px',
				xl: '1200px',
				// Touch devices: no hover, lighter effects (matches the original media query).
				touch: { raw: '(hover: none)' },
			},
			boxShadow: {
				panel: '0 16px 42px rgba(20, 14, 22, .24)',
				stage: '0 24px 64px rgba(20, 14, 22, .42)',
				card: '0 18px 46px rgba(44, 36, 48, .32)',
				soft: '0 4px 12px rgba(20, 14, 22, .18)',
				'soft-lg': '0 6px 18px rgba(20, 14, 22, .24)',
				btn: '0 5px 14px rgba(117, 75, 90, .24)',
				'btn-hover': '0 7px 18px rgba(117, 75, 90, .3)',
			},
			maxWidth: { page: '1920px' },
		},
	},
	plugins: [],
};
