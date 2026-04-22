// Player SPA: join → lobby → captain/radist/crew flow → live play.
import { SHIP_TYPE_INFO, POOL_PICKABLE, shipIcon, hpClass, TEAM_COLORS }
  from "/static/shared/ships.js";

const $ = (id) => document.getElementById(id);
const show = (el) => el.classList.remove("hidden");
const hide = (el) => el.classList.add("hidden");

const qs = new URLSearchParams(location.search);
const state = {
  gid: qs.get("g") || "",
  publicId: qs.get("c") || "",
  joinKey: qs.get("k") || "",
  pid: null,
  token: sessionStorage.getItem("token") || null,
  ws: null,
  data: null,          // latest state from server
  pickedPool: [],      // captain's in-progress selection
  selectedShipId: null,
};

// ---- boot ----------------------------------------------------------------
async function boot() {
  if (!state.gid && state.publicId && state.joinKey) {
    const ok = await resolveGame(state.publicId, state.joinKey);
    if (!ok) return;
  }
  if (!state.gid) {
    show($("screen-connect"));
    return;
  }
  $("hdr-gid").textContent = state.publicId
    ? `${state.publicId}`
    : state.gid;
  if (state.token) {
    // Try reconnect via WS; if token invalid it'll close.
    await connectWS();
  } else {
    show($("screen-join"));
  }
}

$("connect-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const code = $("connect-code").value.trim().toUpperCase();
  const key = $("connect-key").value.trim();
  if (!code || !key) return;
  const ok = await resolveGame(code, key);
  if (!ok) return;
  hide($("screen-connect"));
  show($("screen-join"));
});

async function resolveGame(code, key) {
  const r = await fetch(`/api/play/resolve?code=${encodeURIComponent(code)}&key=${encodeURIComponent(key)}`);
  if (!r.ok) {
    const j = await r.json().catch(() => ({ detail: "Неверный ID/ключ" }));
    $("connect-err").textContent = j.detail || "Не удалось открыть игру";
    show($("connect-err"));
    return false;
  }
  const j = await r.json();
  state.gid = j.gid;
  state.publicId = j.public_id;
  state.joinKey = key;
  $("hdr-gid").textContent = state.publicId || state.gid;
  hide($("connect-err"));
  history.replaceState({}, "", `/play?c=${encodeURIComponent(state.publicId)}&k=${encodeURIComponent(state.joinKey)}`);
  return true;
}

// ---- join ----------------------------------------------------------------
$("join-form").addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const name = $("join-name").value.trim();
  if (!name) return;
  const r = await fetch(`/api/play/${state.gid}/join`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({ detail: "ошибка" }));
    $("join-err").textContent = j.detail;
    show($("join-err"));
    return;
  }
  const j = await r.json();
  state.pid = j.pid;
  state.token = j.token;
  sessionStorage.setItem("token", j.token);
  hide($("screen-join"));
  await connectWS();
});

// ---- websocket ----------------------------------------------------------
function connectWS() {
  return new Promise((resolve) => {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/api/play/${state.gid}/ws?token=${encodeURIComponent(state.token)}`;
    const ws = new WebSocket(url);
    state.ws = ws;
    ws.addEventListener("open", () => resolve());
    ws.addEventListener("close", (ev) => {
      if (ev.code === 4401) {
        // Invalid token — reset.
        sessionStorage.removeItem("token");
        state.token = null;
        show($("screen-join"));
      } else {
        // Try reconnect after a moment.
        setTimeout(() => { if (state.token) connectWS(); }, 1500);
      }
    });
    ws.addEventListener("message", (ev) => {
      const msg = JSON.parse(ev.data);
      if (msg.type === "state") render(msg.data);
    });
  });
}

// Heartbeat so the backend's receive_text() isn't starved.
setInterval(() => {
  if (state.ws && state.ws.readyState === WebSocket.OPEN) {
    state.ws.send("ping");
  }
}, 20000);

// ---- render router -------------------------------------------------------

function render(data) {
  state.data = data;
  state.pid = state.pid || data.you?.pid;
  $("you-label").textContent = data.you
    ? `${data.you.name} · ${roleLabel(data.you.role)}${data.you.team ? " · Team " + data.you.team : ""} · ${data.mode === "normal" ? "обычный" : "продвинутый"}`
    : "—";

  if (data.phase === "lobby") {
    hide($("screen-play"));
    hide($("screen-over"));
    show($("screen-lobby"));
    renderLobby(data);
  } else if (data.phase === "planning") {
    hide($("screen-lobby"));
    hide($("screen-over"));
    show($("screen-play"));
    renderPlay(data);
  } else if (data.phase === "finished") {
    hide($("screen-lobby"));
    hide($("screen-play"));
    show($("screen-over"));
    renderOver(data);
  }
}

function roleLabel(r) {
  return r === "captain" ? "🎖 капитан"
       : r === "radist"  ? "📡 радист"
       : "👥 экипаж";
}

// ---- lobby ---------------------------------------------------------------
function renderLobby(data) {
  const teams = $("lobby-teams");
  teams.innerHTML = "";
  for (const t of data.teams) {
    const b = document.createElement("button");
    b.className = `team-btn team-${t.letter} ${data.you?.team === t.letter ? "selected" : ""}`;
    b.textContent = `${t.letter} · ${t.name} (${8 - t.slots_free}/8)`;
    b.disabled = t.slots_free <= 0 && data.you?.team !== t.letter;
    b.addEventListener("click", () => pickTeam(t.letter));
    teams.appendChild(b);
  }

  const you = $("lobby-you");
  you.innerHTML = "";
  if (data.team) {
    const t = data.team;
    const captain = data.players.find(p => p.pid === t.captain_pid);
    const radist  = data.players.find(p => p.pid === t.radist_pid);
    you.innerHTML = `
      <div class="row">
        <span class="team-pill team-${t.letter}">${t.letter}</span>
        <strong class="grow">${escapeHtml(t.name)}</strong>
        <span class="badge ${t.ready ? 'ok' : ''}">${t.ready ? 'готов' : 'не готов'}</span>
      </div>
      <div class="small muted">
        🎖 ${captain ? escapeHtml(captain.name) : "—"} ·
        📡 ${radist  ? escapeHtml(radist.name)  : "—"}
      </div>
      <div class="small">Экипаж:
        ${t.roster.filter(p => p.role === "crew").map(p =>
          `<span class="roster-pill">${escapeHtml(p.name)}</span>`).join(" ") || `<span class="muted">пусто</span>`}
      </div>`;
  } else {
    you.innerHTML = `<span class="muted small">выберите команду</span>`;
  }

  const isNormal = data.mode === "normal";

  // Role buttons — disable unavailable ones.
  document.querySelectorAll("[data-role]").forEach(b => {
    const r = b.dataset.role;
    b.disabled = !data.you?.team || isNormal;
    b.classList.toggle("hidden", isNormal);
    if (data.team) {
      if (r === "captain" && data.team.captain_pid && data.team.captain_pid !== data.you.pid) b.disabled = true;
      if (r === "radist"  && data.team.radist_pid  && data.team.radist_pid  !== data.you.pid) b.disabled = true;
    }
    b.classList.toggle("primary", data.you?.role === r);
  });

  // Captain panel visibility.
  if (data.you?.role === "captain") {
    show($("captain-setup"));
    renderCaptainSetup(data);
  } else {
    hide($("captain-setup"));
  }
}

async function pickTeam(letter) {
  await fetch(`/api/play/${state.gid}/pick_team?token=${state.token}&team=${letter}`,
              { method: "POST" });
}

document.querySelectorAll("[data-role]").forEach(b => {
  b.addEventListener("click", async () => {
    await fetch(`/api/play/${state.gid}/claim_role?token=${state.token}&role=${b.dataset.role}`,
                { method: "POST" });
  });
});

// --- captain setup --------------------------------------------------------
function renderCaptainSetup(data) {
  const t = data.team;
  const isNormal = data.mode === "normal";
  if ($("team-name-input") !== document.activeElement) {
    $("team-name-input").value = t.name;
  }

  if (isNormal) {
    state.pickedPool = Array(10).fill("Крейсер");
    $("catalog").innerHTML = `<div class="muted small">В обычном режиме у команды фиксированные 10 крейсеров.</div>`;
    $("pool-current").innerHTML = state.pickedPool.map(
      (p) => `<span class="pool-chip">${shipIcon(p)} ${p}</span>`
    ).join("");
    $("pool-hint").textContent = "10/10";
    $("btn-save-pool").disabled = true;
    $("cb-ready").checked = !!t.ready;
    $("cb-ready").disabled = false;
    return;
  }

  $("btn-save-pool").disabled = false;

  // Sync pickedPool to server-side pool the first time we enter the screen.
  if (!state.pickedPool.length && t.pool.length) {
    state.pickedPool = [...t.pool];
  }

  // Catalog.
  const cat = $("catalog");
  cat.innerHTML = "";
  for (const type of POOL_PICKABLE) {
    const info = SHIP_TYPE_INFO[type];
    const count = state.pickedPool.filter(x => x === type).length;
    const card = document.createElement("div");
    card.className = "card" + (count ? " picked" : "");
    card.innerHTML = `
      <div class="row">
        <span class="ship-icon">${info.icon}</span>
        <strong>${type}</strong>
        <span class="grow"></span>
        <span class="badge">${count}</span>
      </div>
      <div class="small muted">${info.role}</div>
      <div class="row action-row">
        <button data-add="${type}">+</button>
        <button data-rm="${type}">−</button>
      </div>`;
    cat.appendChild(card);
  }
  cat.querySelectorAll("[data-add]").forEach(b => b.addEventListener("click", () => {
    if (state.pickedPool.length >= 8) return;
    state.pickedPool.push(b.dataset.add);
    renderCaptainSetup(state.data);
  }));
  cat.querySelectorAll("[data-rm]").forEach(b => b.addEventListener("click", () => {
    const i = state.pickedPool.lastIndexOf(b.dataset.rm);
    if (i >= 0) state.pickedPool.splice(i, 1);
    renderCaptainSetup(state.data);
  }));

  // Current pool display.
  const cur = $("pool-current");
  cur.innerHTML = state.pickedPool.length
    ? state.pickedPool.map((p, i) =>
        `<span class="pool-chip">${shipIcon(p)} ${p} <button data-rmi="${i}" style="padding:0 4px;background:transparent;border:none;color:var(--accent-danger);cursor:pointer;">✕</button></span>`
      ).join("")
    : `<span class="small muted">выберите 8 кораблей из каталога</span>`;
  cur.querySelectorAll("[data-rmi]").forEach(b => b.addEventListener("click", () => {
    state.pickedPool.splice(+b.dataset.rmi, 1);
    renderCaptainSetup(state.data);
  }));

  $("pool-hint").textContent = `${state.pickedPool.length}/8`;

  $("cb-ready").checked = !!t.ready;
  $("cb-ready").disabled = t.pool.length !== 8;
}

$("btn-rename").addEventListener("click", async () => {
  const v = $("team-name-input").value.trim();
  if (!v) return;
  await fetch(`/api/play/${state.gid}/rename_team?token=${state.token}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ name: v }),
  });
});

$("btn-save-pool").addEventListener("click", async () => {
  if (state.pickedPool.length !== 8) {
    alert("Пул должен содержать ровно 8 кораблей");
    return;
  }
  const r = await fetch(`/api/play/${state.gid}/pool?token=${state.token}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ pool: state.pickedPool }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({ detail: "ошибка" }));
    alert(j.detail);
  }
});

$("cb-ready").addEventListener("change", async (ev) => {
  await fetch(`/api/play/${state.gid}/ready?token=${state.token}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ ready: ev.target.checked }),
  });
});

// ---- play (in-game) ------------------------------------------------------
function renderPlay(data) {
  $("play-phase").textContent = data.phase;
  $("play-turn").textContent = data.turn;
  $("play-deadline").textContent = data.planning_deadline
    ? new Date(data.planning_deadline * 1000).toLocaleTimeString() : "—";
  $("play-role-badge").innerHTML = `<span class="role-badge role-${data.you.role}">${roleLabel(data.you.role)}</span>`;

  drawMap(data);
  renderMyShips(data);
  renderOrders(data);
  renderIntel(data);
  renderTurnSummary(data);
}

function renderMyShips(data) {
  const holder = $("my-ships");
  holder.innerHTML = "";
  const mine = data.my_ships
    ? Object.values(data.my_ships)
    : (data.you.role === "crew" ? [] :
       Object.values(data.team?.ships || {}).filter(s =>
         s.id && data.team?.ships?.[s.id]));
  const list = Array.isArray(mine) && mine.length
    ? mine
    : Object.values(data.team?.ships || {})
        .filter(s => s.alive);
  if (!list.length) {
    holder.innerHTML = `<div class="muted small">нет кораблей</div>`;
    return;
  }
  for (const s of list) {
    const row = document.createElement("div");
    row.className = "ship";
    const hpPct = Math.max(0, 100 * (1 - s.hits / s.max_hits));
    const act = data.team?.planned_actions?.[s.id];
    const order = data.team?.orders?.[s.id];
    const sugg = data.team?.suggestions?.[s.id];
    row.innerHTML = `
      <div class="ico">${shipIcon(s.type)}</div>
      <div>
        <div><strong>${escapeHtml(s.name)}</strong> <small>(${s.x},${s.y},${s.z})</small></div>
        <div class="small muted">HP ${s.max_hits - s.hits}/${s.max_hits}${s.is_phased ? ' · <span class="badge phase">👻 ФАЗА</span>' : ''}${s.phase_cooldown > 0 ? ` · <span class="badge cd">cd ${s.phase_cooldown}</span>` : ''}</div>
        <div class="hp-bar"><span class="${hpClass(s.hits, s.max_hits)}" style="width:${hpPct}%"></span></div>
        ${act ? `<div class="small" style="color:var(--accent-success)">→ план: ${escapeHtml(act.action_type)} ${act.target ? act.target.join(',') : ''}</div>` : ''}
        ${order ? `<div class="suggest-banner">🎖 Приказ капитана: <b>${escapeHtml(order.action_type)}</b>${order.target ? ' → ' + order.target.join(',') : ''}${order.note ? ' · ' + escapeHtml(order.note) : ''}</div>` : ''}
        ${sugg && data.you.role === "captain" ? `<div class="suggest-banner">📡 Подсказка радиста: <b>${escapeHtml(sugg.action_type)}</b>${sugg.target ? ' → ' + sugg.target.join(',') : ''}${sugg.note ? ' · ' + escapeHtml(sugg.note) : ''}<button data-promote="${s.id}" style="margin-left:8px;">Принять</button></div>` : ''}
      </div>
      <div class="action-row stack">
        ${ownsShip(data, s.id) ? actionButtons(s.id) : ''}
      </div>`;
    holder.appendChild(row);
  }
  holder.querySelectorAll("[data-plan]").forEach(b => b.addEventListener("click", () => planActionPrompt(b.dataset.plan, b.dataset.type)));
  holder.querySelectorAll("[data-clear]").forEach(b => b.addEventListener("click", () => clearAction(b.dataset.clear)));
  holder.querySelectorAll("[data-promote]").forEach(b => b.addEventListener("click", async () => {
    await fetch(`/api/play/${state.gid}/promote_suggestion?token=${state.token}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ ship_id: b.dataset.promote }),
    });
  }));
}

function ownsShip(data, sid) {
  return data.you.assigned_ships?.includes(sid);
}

function actionButtons(sid) {
  return `
    <button data-plan="${sid}" data-type="move">🚶 Ход</button>
    <button data-plan="${sid}" data-type="shoot">🎯 Стрелять</button>
    <button data-plan="${sid}" data-type="heal">🔥 Heal</button>
    <button data-plan="${sid}" data-type="phase">👻 Фаза</button>
    <button data-clear="${sid}">✕ Отменить</button>`;
}

async function planActionPrompt(sid, actionType) {
  let tx = null, ty = null, tz = null;
  if (actionType !== "phase") {
    const s = prompt(`Цель (x,y,z) для ${actionType}:`);
    if (!s) return;
    const parts = s.split(/[,\s]+/).map(Number);
    if (parts.length !== 3 || parts.some(isNaN)) { alert("Ожидается 3 числа через запятую"); return; }
    [tx, ty, tz] = parts;
  }
  const r = await fetch(`/api/play/${state.gid}/action?token=${state.token}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ship_id: sid, action_type: actionType, tx, ty, tz }),
  });
  if (!r.ok) {
    const j = await r.json().catch(() => ({ detail: "ошибка" }));
    alert(j.detail);
  }
}

async function clearAction(sid) {
  await fetch(`/api/play/${state.gid}/clear_action?token=${state.token}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ship_id: sid }),
  });
}

function renderOrders(data) {
  const title = $("orders-title");
  const holder = $("orders-list");
  holder.innerHTML = "";
  const role = data.you.role;
  if (role === "captain" && data.mode === "normal") {
    title.textContent = "Управление";
    holder.innerHTML = `<div class="small muted">В обычном режиме капитан управляет всеми 10 крейсерами напрямую в блоке "Корабли под управлением".</div>`;
    return;
  }
  if (role === "captain" && data.mode !== "normal") {
    title.textContent = "Приказы (капитан)";
    const ships = Object.values(data.team?.ships || {}).filter(s => s.alive);
    if (!ships.length) { holder.innerHTML = `<div class="muted small">нет кораблей</div>`; return; }
    for (const s of ships) {
      const order = data.team?.orders?.[s.id];
      const row = document.createElement("div");
      row.className = "order";
      row.innerHTML = `
        <div class="ico">${shipIcon(s.type)}</div>
        <div>
          <div><strong>${escapeHtml(s.name)}</strong> <small>(${s.x},${s.y},${s.z})</small></div>
          ${order ? `<div class="small">${escapeHtml(order.action_type)} ${order.target ? '→ ' + order.target.join(',') : ''}${order.note ? ' · ' + escapeHtml(order.note) : ''}</div>` : '<div class="small muted">приказа нет</div>'}
        </div>
        <div class="action-row stack">
          <button data-order="${s.id}">📣 Приказать</button>
          ${order ? `<button data-clearorder="${s.id}">✕</button>` : ''}
        </div>`;
      holder.appendChild(row);
    }
    holder.querySelectorAll("[data-order]").forEach(b => b.addEventListener("click", () => orderPrompt(b.dataset.order, "order")));
    holder.querySelectorAll("[data-clearorder]").forEach(b => b.addEventListener("click", async () => {
      await fetch(`/api/play/${state.gid}/clear_order?token=${state.token}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ship_id: b.dataset.clearorder }),
      });
    }));
  } else if (role === "radist") {
    title.textContent = "Подсказки (радист)";
    const ships = Object.values(data.team?.ships || {}).filter(s => s.alive);
    for (const s of ships) {
      const sug = data.team?.suggestions?.[s.id];
      const row = document.createElement("div");
      row.className = "order";
      row.innerHTML = `
        <div class="ico">${shipIcon(s.type)}</div>
        <div>
          <div><strong>${escapeHtml(s.name)}</strong> <small>(${s.x},${s.y},${s.z})</small></div>
          ${sug ? `<div class="small">${escapeHtml(sug.action_type)} ${sug.target ? '→ ' + sug.target.join(',') : ''}</div>` : '<div class="small muted">нет подсказки</div>'}
        </div>
        <div class="action-row">
          <button data-suggest="${s.id}">💡 Предложить</button>
        </div>`;
      holder.appendChild(row);
    }
    holder.querySelectorAll("[data-suggest]").forEach(b => b.addEventListener("click", () => orderPrompt(b.dataset.suggest, "suggest")));
  } else {
    title.textContent = "Приказы капитана";
    const mine = data.you.assigned_ships || [];
    if (!mine.length) { holder.innerHTML = `<div class="muted small">нет кораблей под управлением</div>`; return; }
    for (const sid of mine) {
      const order = data.team?.orders?.[sid];
      const s = data.team?.ships?.[sid];
      if (!s) continue;
      const row = document.createElement("div");
      row.className = "order";
      row.innerHTML = `
        <div class="ico">${shipIcon(s.type)}</div>
        <div>
          <div><strong>${escapeHtml(s.name)}</strong> <small>(${s.x},${s.y},${s.z})</small></div>
          ${order ? `<div class="small">${escapeHtml(order.action_type)} ${order.target ? '→ ' + order.target.join(',') : ''}${order.note ? ' · ' + escapeHtml(order.note) : ''}</div>` : '<div class="small muted">приказа нет</div>'}
        </div>
        <div class="action-row">
          ${order ? `<button data-follow="${sid}" data-type="${order.action_type}" data-t="${(order.target || []).join(',')}">✔ Выполнить</button>` : ''}
        </div>`;
      holder.appendChild(row);
    }
    holder.querySelectorAll("[data-follow]").forEach(b => b.addEventListener("click", async () => {
      const t = b.dataset.t ? b.dataset.t.split(",").map(Number) : [null, null, null];
      const [tx, ty, tz] = [t[0] ?? null, t[1] ?? null, t[2] ?? null];
      await fetch(`/api/play/${state.gid}/action?token=${state.token}`, {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ ship_id: b.dataset.follow, action_type: b.dataset.type, tx, ty, tz }),
      });
    }));
  }
}

async function orderPrompt(sid, kind) {
  const t = prompt(`Действие (move/shoot/heal/phase/mine/hologram):`);
  if (!t) return;
  let tx = null, ty = null, tz = null;
  if (t !== "phase") {
    const tgt = prompt("Цель (x,y,z):");
    if (!tgt) return;
    const p = tgt.split(/[,\s]+/).map(Number);
    if (p.length !== 3 || p.some(isNaN)) { alert("нужно 3 числа"); return; }
    [tx, ty, tz] = p;
  }
  const note = prompt("Заметка (опционально):") || "";
  const path = kind === "order" ? "order" : "suggest";
  const r = await fetch(`/api/play/${state.gid}/${path}?token=${state.token}`, {
    method: "POST", headers: { "content-type": "application/json" },
    body: JSON.stringify({ ship_id: sid, action_type: t, tx, ty, tz, note }),
  });
  if (!r.ok) { const j = await r.json().catch(() => ({})); alert(j.detail || "ошибка"); }
}

function renderIntel(data) {
  const holder = $("intel-list");
  holder.innerHTML = "";
  const intel = data.intel || {};
  const items = Object.values(intel);
  if (!items.length) { holder.innerHTML = `<div class="muted small">пока нет данных</div>`; return; }
  for (const e of items) {
    const row = document.createElement("div");
    row.className = "ship enemy";
    row.innerHTML = `
      <div class="ico">${shipIcon(e.type || e.ship_type)}</div>
      <div>
        <div><strong>${escapeHtml(e.type || e.ship_type)} ${escapeHtml(e.team || "")}</strong> <small>(${e.x},${e.y},${e.z})</small></div>
        <div class="small muted">замечен на ходу ${e.turn_seen ?? '?'}</div>
      </div>
      <div class="action-row">
        ${data.you.role === "radist" ? `<button data-relay="${e.id}">📡 Капитану</button>` : ''}
      </div>`;
    holder.appendChild(row);
  }
  holder.querySelectorAll("[data-relay]").forEach(b => b.addEventListener("click", async () => {
    await fetch(`/api/play/${state.gid}/share_intel?token=${state.token}`, {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ enemy_ship_id: b.dataset.relay }),
    });
  }));
}

function renderTurnSummary(data) {
  const holder = $("turn-summary");
  const r = data.recent_turn;
  if (!r) { holder.innerHTML = `<span class="muted">пока ни одного хода не прошло</span>`; return; }
  const hits = (r.last_hits || []).map(h =>
    `<div>T${h.turn} ${escapeHtml(h.attacker_name || '')} → ${escapeHtml(h.target_name || '')} ${h.killed ? '✖' : '-' + (h.damage || 1) + 'HP'}</div>`
  ).join("");
  const events = (r.last_events || []).map(e =>
    `<div>${escapeHtml(e.type)} · ${escapeHtml(e.ship_name || '')} ${e.team ? '(' + e.team + ')' : ''}</div>`
  ).join("");
  holder.innerHTML = `${hits || '<div class="muted">нет попаданий</div>'}${events}`;
}

// ---- map canvas ----------------------------------------------------------
const CELL_SIZE = 46;
const MAP_OFFSET_X = 40;
const MAP_OFFSET_Y = 40;
let currentZ = 4;

function drawMap(data) {
  const c = $("map");
  const ctx = c.getContext("2d");
  ctx.fillStyle = "#0c1030";
  ctx.fillRect(0, 0, c.width, c.height);

  // Axis labels.
  ctx.fillStyle = "#9aa3c7";
  ctx.font = "12px DejaVu Sans";
  for (let i = 0; i < 10; i++) {
    ctx.fillText(i, MAP_OFFSET_X + i * CELL_SIZE + CELL_SIZE / 2 - 3, MAP_OFFSET_Y - 8);
    ctx.fillText(i, MAP_OFFSET_X - 16, MAP_OFFSET_Y + i * CELL_SIZE + CELL_SIZE / 2 + 4);
  }

  // Grid.
  ctx.strokeStyle = "#2a355f";
  for (let x = 0; x < 10; x++) {
    for (let y = 0; y < 10; y++) {
      ctx.fillStyle = "#16204a";
      ctx.fillRect(MAP_OFFSET_X + x * CELL_SIZE, MAP_OFFSET_Y + y * CELL_SIZE, CELL_SIZE - 1, CELL_SIZE - 1);
    }
  }

  // Gather ships to draw. Role decides.
  const mine = data.my_ships ? Object.values(data.my_ships) : [];
  const teamShips = Object.values(data.team?.ships || {});
  const intel = Object.values(data.intel || {});

  const toDraw = [];
  if (data.you.role === "crew") {
    // Only own ships + intel (what this crew's ships see).
    for (const s of mine) toDraw.push({ ship: s, kind: "own" });
    for (const s of teamShips.filter(x => !mine.find(m => m.id === x.id))) toDraw.push({ ship: s, kind: "ally" });
    for (const e of intel) toDraw.push({ ship: e, kind: "enemy" });
  } else if (data.you.role === "radist") {
    // All own ships + all intel auto-aggregated.
    for (const s of teamShips) toDraw.push({ ship: s, kind: "ally" });
    for (const e of intel) toDraw.push({ ship: e, kind: "enemy" });
  } else {
    // Captain: own team + only explicitly relayed intel.
    for (const s of teamShips) toDraw.push({ ship: s, kind: "ally" });
    for (const e of intel) toDraw.push({ ship: e, kind: "enemy" });
  }

  for (const { ship: s, kind } of toDraw) {
    if (s.z !== currentZ || !s.alive) continue;
    const cx = MAP_OFFSET_X + s.x * CELL_SIZE;
    const cy = MAP_OFFSET_Y + s.y * CELL_SIZE;
    const color = kind === "enemy" ? "#ff5c7a" : (TEAM_COLORS[data.you.team] || "#4a9dff");
    ctx.fillStyle = color;
    ctx.globalAlpha = kind === "ally" && s.id && !mine.find(m => m.id === s.id) ? 0.45 : 0.95;
    ctx.fillRect(cx + 2, cy + 2, CELL_SIZE - 5, CELL_SIZE - 5);
    ctx.globalAlpha = 1;

    // HP bar.
    if (s.max_hits) {
      const pct = Math.max(0, 1 - s.hits / s.max_hits);
      ctx.fillStyle = "#0c1030";
      ctx.fillRect(cx + 4, cy + CELL_SIZE - 8, CELL_SIZE - 9, 4);
      ctx.fillStyle = pct >= 0.66 ? "#6bff9d" : pct >= 0.33 ? "#ffd24a" : "#ff5c7a";
      ctx.fillRect(cx + 4, cy + CELL_SIZE - 8, (CELL_SIZE - 9) * pct, 4);
    }

    // Icon.
    ctx.fillStyle = "#0c1030";
    ctx.font = "18px DejaVu Sans";
    ctx.textAlign = "center";
    ctx.fillText(shipIcon(s.type || s.ship_type), cx + CELL_SIZE / 2, cy + CELL_SIZE / 2 + 4);
    ctx.textAlign = "start";

    // Ship id + coordinates labels.
    const sid = s.id || "";
    ctx.fillStyle = "#0c1030";
    ctx.font = "9px DejaVu Sans";
    ctx.fillText(sid, cx + 4, cy + 12);
    ctx.fillText(`${s.x},${s.y},${s.z}`, cx + 4, cy + CELL_SIZE - 12);

    // Phase ring.
    if (s.is_phased) {
      ctx.strokeStyle = "#b57bff";
      ctx.setLineDash([3, 3]);
      ctx.lineWidth = 2;
      ctx.strokeRect(cx + 1, cy + 1, CELL_SIZE - 3, CELL_SIZE - 3);
      ctx.setLineDash([]);
    }
  }
}

// Z slider UI (with wheel support).
const zSlider = $("z-slider");
const zLabel = $("z-label");
zSlider.value = String(currentZ);
zLabel.textContent = String(currentZ);
zSlider.addEventListener("input", () => {
  currentZ = Number(zSlider.value || 0);
  zLabel.textContent = String(currentZ);
  if (state.data) drawMap(state.data);
});
$("map-toolbar").addEventListener("wheel", (ev) => {
  ev.preventDefault();
  const dir = ev.deltaY > 0 ? 1 : -1;
  currentZ = Math.max(0, Math.min(9, currentZ + dir));
  zSlider.value = String(currentZ);
  zLabel.textContent = String(currentZ);
  if (state.data) drawMap(state.data);
}, { passive: false });

// ---- game over -----------------------------------------------------------
function renderOver(data) {
  const winner = data.recent_turn?.winner || "ничья";
  $("over-info").innerHTML = `
    <div class="card">
      <h3>Победитель: ${escapeHtml(winner)}</h3>
      <p>Ходов сыграно: ${data.turn}</p>
    </div>`;
}

// ---- help modal ----------------------------------------------------------
$("btn-help").addEventListener("click", () => {
  const body = $("help-body");
  body.innerHTML = "";
  for (const [name, info] of Object.entries(SHIP_TYPE_INFO)) {
    const card = document.createElement("div");
    card.className = "help-card";
    card.innerHTML = `
      <h4>${info.icon} ${name} <span class="small muted">— ${info.role}</span></h4>
      <div class="small">HP: ${info.stats.HP} · move: ${info.stats.move} · атака: ${info.stats["атака"]}</div>
      <ul>${info.abilities.map(a => `<li>${escapeHtml(a)}</li>`).join("")}</ul>`;
    body.appendChild(card);
  }
  $("help-modal").showModal();
});

// ---- utils ---------------------------------------------------------------
function escapeHtml(s) {
  return String(s ?? "").replace(/[&<>"']/g, c =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

boot();
