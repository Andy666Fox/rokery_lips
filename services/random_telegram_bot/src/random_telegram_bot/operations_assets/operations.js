"use strict";

const statusElement = document.querySelector("#status");
const main = document.querySelector("main");
let diagramURL;

async function api(path) {
  let token;
  try { token = JSON.parse(localStorage.getItem("pb_auth") || "{}").token; } catch { /* Invalid local session. */ }
  if (!token) throw new Error("Войдите в Beszel по ссылке сверху, затем вернитесь сюда.");
  const response = await fetch(`api/${path}`, { headers: { Authorization: `Bearer ${token}` }, cache: "no-store" });
  if (!response.ok) {
    if ([401, 403].includes(response.status)) throw new Error("Нужна действующая сессия администратора Beszel. Войдите по ссылке сверху.");
    throw new Error(`Не удалось загрузить данные (${response.status}). Попробуйте обновить страницу позже.`);
  }
  return response;
}

function table(target, data) {
  const element = document.createElement("table");
  const head = element.createTHead().insertRow();
  for (const name of data.columns) { const cell = document.createElement("th"); cell.scope = "col"; cell.textContent = name; head.append(cell); }
  const body = element.createTBody();
  for (const row of data.rows) {
    const tr = body.insertRow();
    for (const name of data.columns) tr.insertCell().textContent = row[name] === null ? "—" : String(row[name]);
  }
  document.querySelector(target).replaceChildren(element);
}

async function refresh() {
  const button = document.querySelector("#refresh");
  button.disabled = true;
  main.hidden = true;
  statusElement.textContent = "Загрузка…";
  try {
    const data = await (await api("database")).json();
    table("#channels", data.tables.channels);
    table("#users", data.tables.users);
    const states = Object.entries(data.verification).map(([state, count]) => `${state}: ${count}`).join(" · ");
    document.querySelector("#progress").textContent = `Всего: ${data.channels} · Активны: ${data.active} · ${states}` + (data.cooldown_until > Date.now() / 1000 ? ` · Пауза Telegram до ${new Date(data.cooldown_until * 1000).toISOString()}` : "");
    main.hidden = false;
    const schema = await (await api("schema/svg")).blob();
    if (diagramURL) URL.revokeObjectURL(diagramURL);
    diagramURL = URL.createObjectURL(schema);
    document.querySelector("#schema").src = diagramURL;
    const logs = await (await api("logs")).json();
    const entries = logs.map(entry => {
      const section = document.createElement("article");
      const title = document.createElement("h3"); title.textContent = `${entry.name} · ${entry.state}`;
      const text = document.createElement("pre"); text.textContent = entry.logs || "Лог пока пуст";
      section.append(title, text); return section;
    });
    document.querySelector("#logs").replaceChildren(...entries);
    main.hidden = false;
    statusElement.textContent = `Обновлено: ${new Date().toLocaleString("ru-RU")}`;
  } catch (error) { statusElement.textContent = error.message; }
  finally { button.disabled = false; }
}

for (const format of ["svg", "mmd"]) document.querySelector(`#${format}`).addEventListener("click", async () => {
  try {
    const blob = await (await api(`schema/${format}`)).blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a"); link.href = url; link.download = `database.er.${format}`; link.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  } catch (error) { statusElement.textContent = error.message; }
});
document.querySelector("#refresh").addEventListener("click", refresh);
refresh();
