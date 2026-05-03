const escapeHtml = (s) => String(s).replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));

async function loadTelegramPost() {
	const el = document.getElementById('tg-post');
	if (!el) return;
	try {
		const r = await fetch('/api/telegram/latest');
		if (!r.ok) throw new Error('http ' + r.status);
		const post = await r.json();

		const parts = [];
		if (post.photo) {
			parts.push(`<img src="${escapeHtml(post.photo)}" alt="" loading="lazy">`);
		}
		if (post.text) {
			parts.push(`<div class="tg-post-text">${post.text}</div>`);
		}
		if (post.date) {
			const d = new Date(post.date);
			parts.push(`<div class="tg-post-meta">${d.toLocaleString()}</div>`);
		}
		const inner = parts.join('');
		el.innerHTML = post.link
			? `<a class="tg-post-link" href="${escapeHtml(post.link)}" target="_blank" rel="noopener">${inner}</a>`
			: inner;
	} catch (e) {
		el.innerHTML = '<div class="side-empty">post unavailable</div>';
	}
}

async function loadCredits() {
	const track = document.getElementById('credits-track');
	if (!track) return;
	try {
		const r = await fetch('/api/credits');
		if (!r.ok) throw new Error('http ' + r.status);
		const rows = await r.json();
		if (!rows.length) {
			track.parentElement.innerHTML = '<div class="side-empty">no credits</div>';
			return;
		}
		const html = rows.map(row =>
			`<div class="credit">
				<span class="author">${escapeHtml(row.author || '')}</span>
				<span class="track">${escapeHtml(row.track || '')}</span>
			</div>`
		).join('');
		track.innerHTML = html + html;
	} catch (e) {
		track.parentElement.innerHTML = '<div class="side-empty">credits unavailable</div>';
	}
}

loadTelegramPost();
loadCredits();
