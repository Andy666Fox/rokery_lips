const REQUEST_TIMEOUT_MS = 8000;
const CREDIT_SCROLL_DURATION_SECONDS = 60;

async function fetchJson(url) {
	const controller = new AbortController();
	const timeout = window.setTimeout(() => controller.abort(), REQUEST_TIMEOUT_MS);

	try {
		const response = await fetch(url, { signal: controller.signal });
		if (!response.ok) {
			throw new Error(`HTTP ${response.status}`);
		}
		return await response.json();
	} finally {
		window.clearTimeout(timeout);
	}
}

function statusElement(message) {
	const status = document.createElement('p');
	status.className = 'side-status';
	status.textContent = message;
	return status;
}

function createPostImage(url) {
	const image = document.createElement('img');
	image.src = url;
	image.alt = 'Изображение из последней публикации Synchronisica';
	image.loading = 'lazy';
	image.decoding = 'async';
	image.addEventListener('error', () => image.remove(), { once: true });
	return image;
}

async function loadTelegramPost() {
	const container = document.getElementById('tg-post-content');
	if (!container) return;

	container.setAttribute('aria-busy', 'true');
	try {
		const post = await fetchJson('/api/telegram/latest');
		const content = document.createElement(post.link ? 'a' : 'article');
		content.className = 'tg-post-link';

		if (post.link) {
			content.href = post.link;
			content.target = '_blank';
			content.rel = 'noopener';
			content.setAttribute('aria-label', 'Открыть последнюю публикацию в Telegram');
		}

		if (post.video) {
			const video = document.createElement('video');
			video.muted = true;
			video.defaultMuted = true;
			video.autoplay = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
			video.loop = true;
			video.playsInline = true;
			video.preload = 'metadata';
			video.setAttribute('aria-label', 'Анимация из последней публикации Synchronisica');
			if (post.photo) video.poster = post.photo;
			video.addEventListener('error', () => {
				if (post.photo) video.replaceWith(createPostImage(post.photo));
				else video.remove();
			}, { once: true });
			video.src = post.video;
			content.append(video);
		} else if (post.photo) {
			content.append(createPostImage(post.photo));
		}

		if (post.text) {
			const text = document.createElement('p');
			text.className = 'tg-post-text';
			text.textContent = post.text;
			content.append(text);
		}

		if (post.date) {
			const date = new Date(post.date);
			if (!Number.isNaN(date.getTime())) {
				const time = document.createElement('time');
				time.className = 'tg-post-meta';
				time.dateTime = post.date;
				time.textContent = date.toLocaleString('ru-RU');
				content.append(time);
			}
		}

		container.replaceChildren(content);
	} catch {
		container.replaceChildren(statusElement('Публикация временно недоступна.'));
	} finally {
		container.setAttribute('aria-busy', 'false');
	}
}

function createCreditsList(rows) {
	const list = document.createElement('ul');
	list.className = 'credits-list';

	for (const row of rows) {
		const item = document.createElement('li');
		item.className = 'credit';

		const title = document.createElement('cite');
		title.className = 'credit-title';
		title.textContent = row.title;

		const artist = document.createElement('span');
		artist.className = 'credit-artist';
		artist.textContent = row.artist;

		item.append(title, artist);
		list.append(item);
	}

	return list;
}

function configureCreditsScroll(track, originalList) {
	const listHeight = originalList.getBoundingClientRect().height;
	const rowGap = Number.parseFloat(window.getComputedStyle(track).rowGap) || 0;
	const distance = listHeight + rowGap;
	if (distance <= 0) return;

	track.style.setProperty('--scroll-distance', `${distance}px`);
	track.style.setProperty('--scroll-duration', `${CREDIT_SCROLL_DURATION_SECONDS}s`);
	track.classList.add('is-scrolling');
}

function setupCreditsScroll(track, originalList) {
	const staticList = window.matchMedia('(prefers-reduced-motion: reduce), (hover: none)');
	const resize = new ResizeObserver(() => configureCreditsScroll(track, originalList));
	const update = () => {
		resize.disconnect();
		track.querySelector('.credits-list-clone')?.remove();
		track.classList.remove('is-scrolling');
		if (staticList.matches) return;

		const clone = originalList.cloneNode(true);
		clone.classList.add('credits-list-clone');
		clone.setAttribute('aria-hidden', 'true');
		track.append(clone);
		resize.observe(originalList);
	};
	staticList.addEventListener('change', update);
	update();
}

async function loadCredits() {
	const track = document.getElementById('credits-track');
	if (!track) return;

	try {
		const rows = await fetchJson('/api/credits');
		if (!Array.isArray(rows) || rows.length === 0) {
			track.replaceChildren(statusElement('Список пока пуст.'));
			return;
		}

		const originalList = createCreditsList(rows);
		track.replaceChildren(originalList);
		setupCreditsScroll(track, originalList);
	} catch {
		track.replaceChildren(statusElement('Список музыки временно недоступен.'));
	}
}

void loadTelegramPost();
void loadCredits();
