// Admin SPA. GM logs in, creates games, monitors them via WebSocket.
import { shipIcon, hpClass, TEAM_COLORS } from "/static/shared/ships.js";

const $ = (id) => document.getElementById(id);
const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

const state = {
  authed: false,
  gid: null,
  ws: null,
  game: null,
};

// ---- auth ---------------------------------------------------------------

async function loadSession() {
  const r = await fetch("/api/admin/session");
  const j = await r.json();
  state.authed = !!j.authenticated;
  updateAuthUI();
  if (state.authed) await loadGames();
}

function updateAuthUI() {
  if (state.authed) {
    hide($("login-panel"));
    show($("games-panel"));
    show($("btn-logout"));
    $("auth-label").textContent = "Админ";
  } else {
    show($("login-panel"));
    hide($("games-panel"));
    hide($("monitor-panel"));
    hide($("btn-logout"));
    $("auth-label").textContent = "Не авторизован";
  }
}

$("login-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const pw = $("password").value;
  const fd = new FormData();
  fd.append("password", pw);
  const r = await fetch("/api/admin/login", { method: "POST", body: fd });
  const errEl = $("login-err");
  if (!r.ok) {
    errEl.textContent = "Неверный пароль";
    show(errEl);
    return;
  }
  hide(errEl);
  $("password").value = "";
  state.authed = true;
  updateAuthUI();
  loadGames();
});

$("btn-logout").addEventListener("click", async () => {
  await fetch("/api/admin/logout", { method: "POST" });
  state.authed = false;
  state.gid = null;
  if (state.ws) { state.ws.close(); state.ws = null; }
  updateAuthUI();
});

// ---- games --------------------------------------------------------------

async function loadGames() {
  const r = await fetch("/api/admin/games");
  if (!r.ok) { state.authed = false; updateAuthUI(); return; }
  const j = await r.json();
  renderGamesList(j.games);
}

function renderGamesList(games) {
  const holder = $("games-list");
  holder.innerHTML = "";
  if (!games.length) {
    const empty = document.createElement("p");
    empty.className = "muted";
    empty.textContent = "Пока нет игр. Создайте новую.";
    holder.appendChild(empty);
    return;
  }
  for (const g of games) {
    const card = document.createElement("div");
    card.className = "card row";
    card.innerHTML = `
      <div class="grow">
        <div><code>${g.public_id || g.gid}</code> <span class="small muted">(key ${g.join_key || "----"})</span></div>
        <div class="small muted">режим <b>${g.mode || "advanced"}</b> · фаза <b>${g.phase}</b> · игроков ${g.players} · ход ${g.turn}</div>
      </div>
      <button data-open="${g.gid}">Открыть</button>
      <button data-del="${g.gid}" class="danger">✕</button>`;
    holder.appendChild(card);
  }
  holder.querySelectorAll("[data-open]").forEach(b =>
    b.addEventListener("click", () => openMonitor(b.dataset.open)));
  holder.querySelectorAll("[data-del]").forEach(b =>
    b.addEventListener("click", async () => {
      if (!confirm(`Удалить игру ${b.dataset.del}?`)) return;
      await fetch(`/api/admin/games/${b.dataset.del}`, { method: "DELETE" });
      loadGames();
    }));
}

$("btn-create").addEventListener("click", async () => {
  const fd = new FormData();
  fd.append("mode", $("create-mode").value);
  const r = await fetch("/api/admin/games", { method: "POST", body: fd });
  if (!r.ok) return;
  const j = await r.json();
  await loadGames();
  openMonitor(j.gid);
});

$("btn-refresh").addEventListener("click", loadGames);

// ---- monitor ------------------------------------------------------------

function openMonitor(gid) {
  state.gid = gid;
  hide($("games-panel"));
  show($("monitor-panel"));
  $("mon-gid").textContent = gid;
  renderLinks();
  bindGmButtons();
  connectWS();
}

$("btn-back").addEventListener("click", () => {
  if (state.ws) { state.ws.close(); state.ws = null; }
  hide($("monitor-panel"));
  show($("games-panel"));
  state.gid = null;
  loadGames();
});

$("btn-force-start").addEventListener("click", async () => {
  if (!state.gid) return;
  const r = await fetch(`/api/admin/games/${state.gid}/start`, { method: "POST" });
  if (!r.ok) {
    const j = await r.json().catch(() => ({ detail: "ошибка" }));
    alert(`Старт не удался: ${j.detail}`);
  }
});
$("btn-force-turn").addEventListener("click", async () => {
  if (!state.gid) return;
  await fetch(`/api/admin/games/${state.gid}/force_turn`, { method: "POST" });
});

async function sendGmCommand(command, payload = {}) {
  if (!state.gid) return;
  const fd = new FormData();
  fd.append("command", command);
  Object.entries(payload).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") fd.append(k, String(v));
  });
  const r = await fetch(`/api/admin/games/${state.gid}/command`, {
    method: "POST",
    body: fd,
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({ detail: "ошибка" }));
    alert(j.detail || "Команда не выполнена");
  }
}

function bindGmButtons() {
  if ($("btn-gm-start").dataset.bound) return;
  $("btn-gm-start").dataset.bound = "1";
  $("btn-gm-start").addEventListener("click", () => sendGmCommand("start_turn"));
  $("btn-gm-end").addEventListener("click", () => sendGmCommand("end_planning"));
  $("btn-gm-stop").addEventListener("click", () => {
    if (confirm("Остановить игру?")) sendGmCommand("stop");
  });
  $("btn-gm-timeout").addEventListener("click", () => {
    sendGmCommand("set_timeout", { seconds: $("gm-timeout-seconds").value });
  });
  $("btn-gm-override").addEventListener("click", () => {
    sendGmCommand("override_ship", {
      ship_id: $("gm-ship-id").value.trim(),
      x: $("gm-x").value,
      y: $("gm-y").value,
      z: $("gm-z").value,
      hits: $("gm-hits").value,
      alive: $("gm-alive").checked,
    });
  });
  $("map-z").addEventListener("input", () => {
    $("map-z-label").textContent = $("map-z").value;
    if (state.game) renderMap(state.game);
  });
}

function renderLinks() {
  const origin = location.origin;
  const pub = state.game?.public_id || "";
  const key = state.game?.join_key || "";
  const playUrl = pub && key
    ? `${origin}/play?c=${encodeURIComponent(pub)}&k=${encodeURIComponent(key)}`
    : `${origin}/play?g=${state.gid}`;
  $("mon-links").innerHTML = `
    <a class="btn" href="${playUrl}" target="_blank">Ссылка для игроков</a>
    <input readonly value="${playUrl}" style="width:220px;">
    ${pub ? `<span class="small muted">ID: <b>${pub}</b> · ключ: <b>${key}</b></span>` : ""}`;
}

function connectWS() {
  const proto = location.protocol === "https:" ? "wss" : "ws";
  state.ws = new WebSocket(`${proto}://${location.host}/api/admin/games/${state.gid}/ws`);
  state.ws.addEventListener("message", (ev) => {
    const msg = JSON.parse(ev.data);
    if (msg.type === "state") renderMonitor(msg.data);
  });
  state.ws.addEventListener("close", () => { state.ws = null; });
}

function renderMonitor(g) {
  state.game = g;
  renderLinks();
  $("mon-phase").textContent = g.phase;
  $("mon-turn").textContent = g.turn;
  $("mon-players").textContent = (g.players || []).length;
  const dd = g.planning_deadline ? new Date(g.planning_deadline * 1000) : null;
  $("mon-deadline").textContent = dd ? dd.toLocaleTimeString() : "—";
  $("mon-timeout").textContent = g.planning_timeout || "—";
  if (g.planning_timeout) $("gm-timeout-seconds").value = g.planning_timeout;

  if (g.phase === "lobby") {
    show($("btn-force-start"));
    hide($("btn-force-turn"));
  } else if (g.phase === "planning") {
    hide($("btn-force-start"));
    show($("btn-force-turn"));
  } else {
    hide($("btn-force-start"));
    hide($("btn-force-turn"));
  }

  const teams = $("mon-teams");
  teams.innerHTML = "";
  for (const t of g.teams) {
    const roster = (g.players || []).filter(p => p.team === t.letter);
    const capName = roster.find(p => p.role === "captain")?.name || "—";
    const radName = roster.find(p => p.role === "radist")?.name  || "—";
    const crewList = roster.filter(p => p.role === "crew").map(p => p.name).join(", ") || "пусто";
    const card = document.createElement("div");
    card.className = `card team-card team-${t.letter}-bg`;
    card.innerHTML = `
      <div class="letter">${t.letter}</div>
      <div class="stack">
        <h3>${escapeHtml(t.name)} <span class="small muted">· ${roster.length}/8</span></h3>
        <div class="small">
          <span class="roster-pill"><span class="role-badge role-captain">капитан</span> ${escapeHtml(capName)}</span>
          <span class="roster-pill"><span class="role-badge role-radist">радист</span> ${escapeHtml(radName)}</span>
        </div>
        <div class="small muted">Экипаж: ${escapeHtml(crewList)}</div>
        <div class="pool-line">${
          t.pool.length
            ? t.pool.map(p => `<span class="pool-chip">${shipIcon(p)} ${p}</span>`).join("")
            : `<span class="small muted">пул не выбран</span>`
        }</div>
      </div>
      <div class="row">
        <span class="badge ${t.ready ? 'ok' : ''}">${t.ready ? 'готов' : 'не готов'}</span>
      </div>`;
    teams.appendChild(card);
  }

  // log
  const log = $("mon-log");
  log.innerHTML = "";
  const hits = g.hit_history || [];
  for (const h of hits.slice(-200)) {
    const line = document.createElement("div");
    line.className = "log-line";
    const attacker = h.attacker_team ? `<span class="team-pill team-${h.attacker_team.slice(-1)}">${h.attacker_team}</span>` : "";
    const victim = h.target_team ? `<span class="team-pill team-${h.target_team.slice(-1)}">${h.target_team}</span>` : "";
    line.innerHTML = `T${h.turn} ${attacker} ${escapeHtml(h.attacker_name || "")} → ${victim} ${escapeHtml(h.target_name || "")} ${h.killed ? "✖УБИТ" : `-${h.damage || 1}HP`}`;
    log.appendChild(line);
  }
  if (!hits.length) log.innerHTML = `<div class="muted small">нет событий</div>`;
  renderMap(g);
}

function renderMap(g) {
  const holder = $("gm-map");
  holder.innerHTML = "";
  const z = Number($("map-z").value || 0);
  const ships = Object.entries(g.ships || {});
  for (let y = 0; y < 10; y++) {
    for (let x = 0; x < 10; x++) {
      const cell = document.createElement("button");
      cell.type = "button";
      cell.className = "gm-cell";
      const ship = ships.find(([, s]) => s.x === x && s.y === y && s.z === z);
      if (ship) {
        const [sid, s] = ship;
        const letter = (s.team || "Team A").slice(-1);
        cell.classList.add(`team-${letter}`);
        if (!s.alive) cell.classList.add("dead");
        const hp = Math.max(0, (s.max_hits || 1) - (s.hits || 0));
        cell.innerHTML = `${shipIcon(s.type)}<span class="hp-badge">${sid} ${hp}/${s.max_hits || 1}</span>`;
        cell.title = `${sid} (${s.x},${s.y},${s.z})`;
        cell.addEventListener("click", () => {
          $("gm-ship-id").value = sid;
          $("gm-x").value = s.x;
          $("gm-y").value = s.y;
          $("gm-z").value = s.z;
          $("gm-hits").value = s.hits || 0;
          $("gm-alive").checked = !!s.alive;
        });
      } else {
        cell.textContent = "·";
      }
      holder.appendChild(cell);
    }
  }
}

function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// ---- boot ----------------------------------------------------------------
loadSession();
setInterval(() => { if (state.authed && !state.gid) loadGames(); }, 5000);
