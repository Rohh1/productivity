// Drive the AI through a FAKE support-line IVR — no Twilio, no phone.
// Needs only ANTHROPIC_API_KEY set for the running server.
//
//   Terminal 1:  cd ai-caller && npm start
//   Terminal 2:  cd ai-caller && npm run simulate
//
// Watch the AI navigate the menu, enter the (fake) account number, and read
// back the answer. Override the base URL with SIM_BASE=http://localhost:3000.

const BASE = (process.env.SIM_BASE || "http://localhost:3000").replace(/\/+$/, "");

// A made-up bank IVR. The "account number" here is fake — never a real one.
const scenario = {
  calleeName: "Example Bank support line",
  goal: "Find out my current account balance and whether autopay is turned on, then hang up.",
  accountNumber: "48291",
  speakFirst: false,
  ivrScript: [
    "Thank you for calling Example Bank. For English, press 1. Para espanol, oprima dos.",
    "Main menu. For your account balance, press 2. To make a payment, press 3. To speak with an agent, press 0.",
    "Please enter your account number followed by the pound sign.",
    "Thank you. Your current balance is 152 dollars and 30 cents. Autopay is currently turned on. Is there anything else I can help you with?",
  ],
};

async function j(method, path, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const text = await res.text();
  let data;
  try { data = JSON.parse(text); } catch { data = text; }
  if (!res.ok) throw new Error(`${method} ${path} → ${res.status}: ${typeof data === "string" ? data : data.error}`);
  return data;
}

async function form(path, params) {
  // /voice/* endpoints are Twilio webhooks: form-encoded.
  await fetch(`${BASE}${path}`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams(params).toString(),
  });
}

async function getCall(id) {
  const { call } = await j("GET", `/api/calls/${id}`);
  return call;
}

const TERMINAL = new Set(["completed", "no-response", "error", "voicemail"]);

async function main() {
  console.log(`\n▶ Simulating a call to: ${scenario.calleeName}`);
  console.log(`  Goal: ${scenario.goal}`);
  console.log(`  Account number (fake): ${scenario.accountNumber}\n`);

  let health;
  try {
    health = await j("GET", "/api/config");
  } catch {
    console.error(`Could not reach the server at ${BASE}. Start it with: npm start`);
    process.exit(1);
  }
  if (!health || health.missing?.includes("ANTHROPIC_API_KEY")) {
    console.error("ANTHROPIC_API_KEY is not set on the server. Set it in .env and restart, then rerun.");
    process.exit(1);
  }

  const { call } = await j("POST", "/api/simulate", scenario);
  const id = call.id;

  // The IVR answers and starts talking (listen-first).
  await form(`/voice/answer?callId=${id}`, {});

  let seen = 0;
  for (const line of scenario.ivrScript) {
    console.log(`☎  IVR: ${line}`);
    await form(`/voice/respond?callId=${id}`, { SpeechResult: line });

    const c = await getCall(id);
    for (const turn of c.turns.slice(seen)) {
      if (turn.role === "assistant") console.log(`🤖 AI:  ${turn.text}`);
    }
    seen = c.turns.length;

    if (TERMINAL.has(c.status)) break;
  }

  const final = await getCall(id);
  // If the AI hasn't ended yet, nudge it once with the IVR's closing line.
  if (!TERMINAL.has(final.status)) {
    console.log(`☎  IVR: No, that's all. Goodbye.`);
    await form(`/voice/respond?callId=${id}`, { SpeechResult: "No, that's all. Goodbye." });
    const c = await getCall(id);
    for (const turn of c.turns.slice(seen)) {
      if (turn.role === "assistant") console.log(`🤖 AI:  ${turn.text}`);
    }
  }

  const done = await getCall(id);
  console.log(`\n■ Call status: ${done.status}`);
  console.log(`■ Result the AI got back: ${done.result || "(none recorded)"}\n`);
}

main().catch((err) => {
  console.error("\nSimulation error:", err.message);
  process.exit(1);
});
