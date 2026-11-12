const REQUEST_TIMEOUT_MS = 8000;
const CREDIT_SCROLL_DURATION_SECONDS = 60;
const POST_MEDIA_CLASSES = 'photo-filter mb-3 block aspect-[4/3] h-auto max-h-[260px] w-full shrink-0 rounded-[7px] object-cover';

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
	image.className = POST_MEDIA_CLASSES;
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
		content.className = 'group flex h-full min-h-0 flex-col overflow-hidden';

		if (post.link) {
			content.href = post.link;
			content.target = '_blank';
			content.rel = 'noopener';
			content.setAttribute('aria-label', 'Открыть последнюю публикацию в Telegram');
		}

		if (post.video) {
			const video = document.createElement('video');
			video.className = POST_MEDIA_CLASSES;
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
			text.className = 'line-clamp-[8] min-h-0 shrink whitespace-pre-wrap text-base leading-[1.55] text-ink transition-colors duration-150 [overflow-wrap:anywhere] group-hover:text-accent-dark sm:text-[.86rem]';
			text.textContent = post.text;
			content.append(text);
		}

		if (post.date) {
			const date = new Date(post.date);
			if (!Number.isNaN(date.getTime())) {
				const time = document.createElement('time');
				time.className = 'mt-3 block shrink-0 text-[.875rem] text-muted sm:text-[.76rem]';
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
	list.className = 'flex shrink-0 flex-col gap-[1.1rem] px-1 py-2';

	for (const row of rows) {
		const item = document.createElement('li');
		item.className = 'text-center text-base leading-[1.4] [overflow-wrap:anywhere] sm:text-[.85rem]';

		const title = document.createElement('cite');
		title.className = 'block font-semibold not-italic tracking-[.01em] text-ink';
		title.textContent = row.title;

		const artist = document.createElement('span');
		artist.className = 'mt-[.15rem] block text-[.875rem] text-muted sm:text-[.8rem]';
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

const randomButton = document.getElementById('random-channel-button');
randomButton?.addEventListener('click', async () => {
	const result = document.getElementById('random-channel-result');
	if (!result) return;
	randomButton.disabled = true;
	result.textContent = 'Ищем канал…';
	try {
		const response = await fetch('/api/random/channel', {cache: 'no-store', signal: AbortSignal.timeout(5000)});
		if (!response.ok) {
			const messages = {404: 'Каталог пока пуст.', 429: 'Подождите немного перед следующим запросом.'};
			result.textContent = messages[response.status] || 'Сервис временно недоступен. Попробуйте позже.';
			return;
		}
		const data = await response.json();
		if (!/^[a-z][a-z0-9_]{4,31}$/i.test(data.channel?.username)) throw new Error('Invalid channel');
		const link = document.createElement('a');
		link.className = 'underline underline-offset-[.2em]';
		link.href = `https://t.me/${data.channel.username}`;
		link.target = '_blank';
		link.rel = 'noopener noreferrer';
		link.textContent = `@${data.channel.username}`;
		result.replaceChildren(link);
	} catch {
		result.textContent = 'Не удалось получить канал. Попробуйте ещё раз.';
	} finally {
		randomButton.disabled = false;
	}
});

const pinballButton = document.getElementById('pinball-button');
pinballButton?.addEventListener('click', () => {
	const frame = document.getElementById('pinball-frame');
	if (!frame) return;
	// The game is ~40 MB, so it is only requested after an explicit click.
	const game = document.createElement('iframe');
	game.className = 'absolute inset-0 block h-full w-full border-0';
	game.src = '/pinball/';
	game.title = 'Пинбол';
	game.allow = 'fullscreen; autoplay';
	game.addEventListener('load', () => game.focus(), { once: true });
	frame.replaceChildren(game);
}, { once: true });
