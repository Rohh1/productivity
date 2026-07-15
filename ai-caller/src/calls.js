import { randomUUID } from "node:crypto";

// In-memory call store. Fine for a single-process demo; swap for a database
// if you need calls to survive a restart or run multiple instances.
const calls = new Map();

export function createCall(data) {
  const id = randomUUID();
  const call = {
    id,
    to: data.to,
    calleeName: data.calleeName || "",
    goal: data.goal,
    persona: data.persona || "",
    callerName: data.callerName || "",
    status: "queued", // queued | ringing | in-progress | completed | voicemail | no-response | error | busy | no-answer | canceled | failed
    twilioSid: null,
    twilioStatus: null,
    opening: "",
    turns: [], // { role: "assistant" | "user", text, at }
    emptyCount: 0,
    error: null,
    createdAt: new Date().toISOString(),
    endedAt: null,
  };
  calls.set(id, call);
  return call;
}

export function getCall(id) {
  return calls.get(id);
}

export function listCalls() {
  return [...calls.values()].sort((a, b) => b.createdAt.localeCompare(a.createdAt));
}

export function addTurn(id, role, text) {
  const call = calls.get(id);
  const clean = (text || "").trim();
  if (!call || !clean) return;
  call.turns.push({ role, text: clean, at: new Date().toISOString() });
}

export function updateCall(id, patch) {
  const call = calls.get(id);
  if (!call) return;
  Object.assign(call, patch);
}
