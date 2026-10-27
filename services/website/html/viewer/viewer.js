const configUrl = new URL('../model-viewer.json', import.meta.url);
const stage = document.getElementById('model-stage');
const status = document.getElementById('model-status');
const statusText = document.getElementById('model-status-text');
const retry = document.getElementById('retry-model');
const reset = document.getElementById('reset-view');
const fullscreen = document.getElementById('fullscreen');
const motion = window.matchMedia('(prefers-reduced-motion: reduce)');
const LOAD_TIMEOUT_MS = 90000;
let timeout;
let failed = false;

function showError(text) {
	failed = true;
	window.clearTimeout(timeout);
	status.hidden = false;
	statusText.textContent = text;
	retry.hidden = false;
	stage.setAttribute('aria-busy', 'false');
	reset.disabled = true;
	fullscreen.disabled = true;
}

function assetUrl(value) {
	if (typeof value !== 'string' || !value.trim()) throw new Error('invalid_config');
	const url = new URL(value, configUrl);
	if ((url.protocol !== 'https:' && url.origin !== window.location.origin) || url.username || url.password) {
		throw new Error('invalid_config');
	}
	return url.href;
}

async function loadModel() {
	timeout = window.setTimeout(() => showError('Модель загружается слишком долго. Проверьте соединение и попробуйте ещё раз.'), LOAD_TIMEOUT_MS);
	try {
		const response = await fetch(configUrl, { cache: 'no-store', signal: AbortSignal.timeout(8000) });
		if (!response.ok) throw new Error('invalid_config');
		const config = await response.json();
		const src = assetUrl(config.src);
		const poster = config.poster ? assetUrl(config.poster) : null;
		if (!/\.(glb|gltf)$/i.test(new URL(src).pathname)) throw new Error('invalid_config');
		if (typeof config.title !== 'string' || typeof config.alt !== 'string') throw new Error('invalid_config');
		const { ModelViewerElement } = await import('../vendor/model-viewer/model-viewer.min.js');
		ModelViewerElement.dracoDecoderLocation = new URL('../vendor/model-viewer/draco/', import.meta.url).href;
		ModelViewerElement.ktx2TranscoderLocation = new URL('../vendor/model-viewer/basis/', import.meta.url).href;
		ModelViewerElement.meshoptDecoderLocation = new URL('../vendor/model-viewer/meshopt_decoder.js', import.meta.url).href;
		if (failed) return;
		const viewer = document.createElement('model-viewer');
		const orbit = config.cameraOrbit || '30deg 75deg 105%';
		const target = config.cameraTarget || 'auto auto auto';
		const fieldOfView = config.fieldOfView || '30deg';
		viewer.id = 'model-viewer';
		viewer.alt = config.alt;
		viewer.setAttribute('camera-controls', '');
		viewer.setAttribute('touch-action', 'pan-y');
		viewer.setAttribute('interaction-prompt', 'none');
		viewer.setAttribute('loading', 'eager');
		viewer.setAttribute('shadow-intensity', '1');
		viewer.cameraOrbit = orbit;
		viewer.cameraTarget = target;
		viewer.fieldOfView = fieldOfView;
		if (poster) viewer.poster = poster;
		const updateMotion = () => { viewer.autoRotate = config.autoRotate === true && !motion.matches; };
		updateMotion();
		motion.addEventListener('change', updateMotion);
		viewer.addEventListener('load', () => {
			if (failed) return;
			window.clearTimeout(timeout);
			status.hidden = true;
			stage.setAttribute('aria-busy', 'false');
			reset.disabled = false;
			fullscreen.disabled = false;
		});
		viewer.addEventListener('progress', (event) => {
			if (!failed) statusText.textContent = `Загрузка модели… ${Math.round(event.detail.totalProgress * 100)}%`;
		});
		viewer.addEventListener('error', (event) => {
			showError(event.detail.type === 'webglcontextlost'
				? 'Не удалось продолжить 3D-просмотр. Попробуйте открыть модель ещё раз.'
				: 'Не удалось загрузить модель. Попробуйте ещё раз позже.');
		});
		reset.addEventListener('click', () => {
			viewer.cameraOrbit = orbit;
			viewer.cameraTarget = target;
			viewer.fieldOfView = fieldOfView;
			viewer.jumpCameraToGoal();
		});
		document.getElementById('model-title').textContent = config.title;
		if (config.credit) {
			const credit = document.getElementById('model-credit');
			credit.textContent = config.credit.label;
			credit.href = assetUrl(config.credit.url);
			credit.hidden = false;
		}
		stage.prepend(viewer);
		viewer.src = src;
	} catch (error) {
		console.error('Model viewer initialization failed', error);
		showError('Просмотр модели временно недоступен. Попробуйте ещё раз позже.');
	}
}

retry.addEventListener('click', () => window.location.reload());
fullscreen.hidden = !document.fullscreenEnabled;
fullscreen.addEventListener('click', async () => {
	try {
		if (document.fullscreenElement) await document.exitFullscreen();
		else await document.documentElement.requestFullscreen();
	} catch {
		fullscreen.hidden = true;
	}
});
document.addEventListener('fullscreenchange', () => {
	const label = document.fullscreenElement ? 'Выйти из полноэкранного режима' : 'Развернуть на весь экран';
	fullscreen.setAttribute('aria-label', label);
	fullscreen.title = label;
});
void loadModel();
