const form = document.getElementById("call-form");
const submitBtn = document.getElementById("submit-btn");
const formError = document.getElementById("form-error");
const callsEl = document.getElementById("calls");
const banner = document.getElementById("config-banner");

let callerName = "";
const activePolls = new Map(); // callId -> interval

// Statuses that mean the call is over and no longer needs polling.
const TERMINAL = new Set([
  "completed", "voicemail", "no-response", "error",
  "busy", "no-answer", "canceled", "failed",
]);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]
  ));
}

function statusMeta(status) {
  switch (status) {
    case "queued": return { cls: "warn", label: "Queued", live: false };
    case "ringing": return { cls: "live", label: "Ringing", live: true };
    case "in-progress": return { cls: "live", label: "In call", live: true };
    case "completed": return { cls: "ok", label: "Completed", live: false };
    case "voicemail": return { cls: "warn", label: "Voicemail", live: false };
    case "no-response": return { cls: "warn", label: "No response", live: false };
    case "no-answer": return { cls: "warn", label: "No answer", live: false };
    case "busy": return { cls: "warn", label: "Busy", live: false };
    case "canceled": return { cls: "warn", label: "Canceled", live: false };
    case "failed": return { cls: "err", label: "Failed", live: false };
    case "error": return { cls: "err", label: "Error", live: false };
    default: return { cls: "warn", label: status || "Unknown", live: false };
  }
}

function renderTurns(turns) {
  if (!turns || !turns.length) {
    return `<div class="transcript-empty">Waiting for the conversation to start…</div>`;
  }
  return turns.map((t) => {
    const isAI = t.role === "assistant";
    const isKey = isAI && /^(Pressed |Entered )/.test(t.text);
    const who = isAI ? (isKey ? "AI · keypad" : "AI") : "Them";
    const cls = isAI ? (isKey ? "assistant keys" : "assistant") : "user";
    return `<div class="turn ${cls}">
      <span class="who">${who}</span>${esc(t.text)}
    </div>`;
  }).join("");
}

function renderCall(call) {
  const meta = statusMeta(call.status);
  const title = call.calleeName ? esc(call.calleeName) : "Call";
  const dot = meta.live ? '<span class="dot"></span>' : "";
  return `<div class="call" id="call-${esc(call.id)}">
    <div class="call-head">
      <div>
        <div class="call-title">${title}</div>
        <div class="call-num">${esc(call.to)}</div>
      </div>
      <span class="status ${meta.cls}">${dot}${esc(meta.label)}</span>
    </div>
    <div class="call-goal">Goal: ${esc(call.goal)}</div>
    ${call.result ? `<div class="result"><span class="result-label">Result</span>${esc(call.result)}</div>` : ""}
    ${call.error ? `<div class="error">${esc(call.error)}</div>` : ""}
    <div class="transcript">${renderTurns(call.turns)}</div>
  </div>`;
}

function upsertCall(call) {
  const existing = document.getElementById(`call-${call.id}`);
  const html = renderCall(call);
  if (existing) {
    existing.outerHTML = html;
  } else {
    const empty = callsEl.querySelector(".empty");
    if (empty) empty.remove();
    callsEl.insertAdjacentHTML("afterbegin", html);
  }
  if (!TERMINAL.has(call.status)) startPolling(call.id);
  else stopPolling(call.id);
}

function startPolling(id) {
  if (activePolls.has(id)) return;
  const interval = setInterval(() => pollCall(id), 1500);
  activePolls.set(id, interval);
}

function stopPolling(id) {
  const interval = activePolls.get(id);
  if (interval) {
    clearInterval(interval);
    activePolls.delete(id);
  }
}

async function pollCall(id) {
  try {
    const res = await fetch(`/api/calls/${id}`);
    if (!res.ok) return;
    const { call } = await res.json();
    upsertCall(call);
  } catch (_) { /* transient network error; try again next tick */ }
}

async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    callerName = cfg.callerName || "";
    if (!cfg.configured) {
      banner.classList.remove("hidden");
      banner.innerHTML = `This server isn't fully configured yet. Missing: <strong>${esc(cfg.missing.join(", "))}</strong>. Set them in <code>.env</code> (see <code>.env.example</code>) and restart.`;
      submitBtn.disabled = true;
    }
  } catch (_) { /* ignore */ }
}

async function loadCalls() {
  try {
    const res = await fetch("/api/calls");
    const { calls } = await res.json();
    calls.slice().reverse().forEach(upsertCall);
  } catch (_) { /* ignore */ }
}

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  formError.textContent = "";
  submitBtn.disabled = true;
  submitBtn.textContent = "Placing…";

  const payload = {
    to: document.getElementById("to").value.trim(),
    calleeName: document.getElementById("calleeName").value.trim(),
    goal: document.getElementById("goal").value.trim(),
    persona: document.getElementById("persona").value.trim(),
    accountNumber: document.getElementById("accountNumber").value.trim(),
    // IVR mode = listen first; unchecked = we speak first (reaching a human).
    speakFirst: !document.getElementById("ivrMode").checked,
  };

  try {
    const res = await fetch("/api/calls", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json();
    if (!res.ok) {
      formError.textContent = data.error || "Something went wrong.";
      if (data.call) upsertCall(data.call);
    } else {
      upsertCall(data.call);
      form.reset();
    }
  } catch (err) {
    formError.textContent = "Network error — is the server running?";
  } finally {
    submitBtn.disabled = false;
    submitBtn.textContent = "Place call";
  }
});

loadConfig();
loadCalls();
