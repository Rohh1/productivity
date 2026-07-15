import express from "express";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { config, missingConfig } from "./src/config.js";
import { createCall, getCall, listCalls, addTurn, updateCall, publicCall } from "./src/calls.js";
import { generateOpening, generateAction } from "./src/anthropic.js";
import { placeCall, validateTwilio } from "./src/twilio.js";
import { listen, listenOnly, pressAndListen, speakAndHangup, plainHangup } from "./src/twiml.js";
import { describePress } from "./src/util.js";

const __dirname = dirname(fileURLToPath(import.meta.url));
const app = express();

app.use(express.urlencoded({ extended: false })); // Twilio webhooks (form-encoded)
app.use(express.json()); // browser API (JSON)
app.use(express.static(join(__dirname, "public")));

const PHONE_RE = /^\+?[1-9]\d{6,14}$/;
const normalize = (n) => String(n || "").replace(/[\s().\-]/g, "");

function sendTwiml(res, vr) {
  res.type("text/xml").send(vr.toString());
}

// ---------------------------------------------------------------------------
// Browser API
// ---------------------------------------------------------------------------

app.get("/health", (_req, res) => res.json({ ok: true }));

app.get("/api/config", (_req, res) => {
  const missing = missingConfig();
  res.json({ configured: missing.length === 0, missing, callerName: config.callerName });
});

app.get("/api/calls", (_req, res) => res.json({ calls: listCalls().map(publicCall) }));

app.get("/api/calls/:id", (req, res) => {
  const call = getCall(req.params.id);
  if (!call) return res.status(404).json({ error: "Call not found" });
  res.json({ call: publicCall(call) });
});

// Validate + build a call record from a request body. Returns { call } or { error }.
function buildCall(body) {
  const { to, goal, calleeName, persona, accountNumber, speakFirst } = body || {};
  const simulated = Boolean(body?.simulated);
  const number = normalize(to);
  if (!simulated && !PHONE_RE.test(number)) {
    return { error: "Enter a valid phone number in E.164 format, e.g. +14155550123." };
  }
  if (!goal || !String(goal).trim()) {
    return { error: "Describe the goal of the call." };
  }
  const call = createCall({
    to: number || "simulated",
    goal: String(goal).trim(),
    calleeName: (calleeName || "").trim(),
    persona: (persona || "").trim(),
    accountNumber: String(accountNumber || "").replace(/[^0-9*#]/g, ""),
    speakFirst: Boolean(speakFirst),
    callerName: config.callerName,
    simulated: Boolean(body?.simulated),
  });
  return { call };
}

app.post("/api/calls", async (req, res) => {
  const missing = missingConfig();
  if (missing.length) {
    return res.status(400).json({ error: `Server is not fully configured. Missing: ${missing.join(", ")}` });
  }

  const { call, error } = buildCall(req.body);
  if (error) return res.status(400).json({ error });

  // When we speak first (reaching a human), precompute the opening line so
  // there's no AI latency on pickup. IVRs speak first, so we skip it there.
  if (call.speakFirst) {
    try {
      call.opening = await generateOpening(call);
    } catch (err) {
      console.error("Opening generation failed:", err);
      call.opening = `Hi${call.calleeName ? " " + call.calleeName : ""}, this is an assistant calling on behalf of ${config.callerName || "a client"}.`;
    }
  }

  try {
    const sid = await placeCall(call);
    updateCall(call.id, { twilioSid: sid, status: "ringing" });
  } catch (err) {
    console.error("Failed to place call:", err);
    updateCall(call.id, { status: "error", error: String(err?.message || err) });
    return res.status(502).json({ error: `Could not place the call: ${err?.message || err}`, call: publicCall(call) });
  }

  res.status(201).json({ call: publicCall(call) });
});

// Create a call WITHOUT dialing Twilio, for local testing of the AI's IVR
// logic. Only needs an Anthropic key; the /voice endpoints are then driven by
// simulate.mjs. No Twilio account, public URL, or phone required.
app.post("/api/simulate", (req, res) => {
  if (!config.anthropicApiKey) {
    return res.status(400).json({ error: "Set ANTHROPIC_API_KEY to run a simulation." });
  }
  const { call, error } = buildCall({ ...req.body, simulated: true });
  if (error) return res.status(400).json({ error });
  updateCall(call.id, { status: "in-progress" });
  res.status(201).json({ call: publicCall(call) });
});

// ---------------------------------------------------------------------------
// Twilio voice webhooks
// ---------------------------------------------------------------------------

// The other end picked up.
app.post("/voice/answer", validateTwilio, (req, res) => {
  const call = getCall(req.query.callId);
  if (!call) {
    return sendTwiml(res, plainHangup("Sorry, this call is no longer available. Goodbye."));
  }

  // Voicemail detection only applies when we're calling a human. On an IVR
  // (listen-first) the "machine" IS the thing we want to talk to, so ignore it.
  if (call.speakFirst && String(req.body.AnsweredBy || "").startsWith("machine")) {
    updateCall(call.id, { status: "voicemail", endedAt: new Date().toISOString() });
    return sendTwiml(res, speakAndHangup(call.opening || "Sorry I missed you. I'll try again later. Goodbye."));
  }

  updateCall(call.id, { status: "in-progress" });

  if (call.speakFirst) {
    const opening = call.opening || "Hello!";
    addTurn(call.id, "assistant", opening);
    return sendTwiml(res, listen(call.id, opening));
  }
  // IVR / support line: listen to what they say first.
  sendTwiml(res, listenOnly(call.id));
});

// Shared handler for a captured turn of the other end's speech. Used by the
// Twilio webhook and by the simulator.
async function handleTurn(call, speech) {
  // No speech captured — keep listening silently (an IVR may just be pausing),
  // and give up after several empty turns rather than talking over it.
  if (!speech) {
    call.emptyCount = (call.emptyCount || 0) + 1;
    if (call.emptyCount >= 5) {
      const bye = "I haven't heard anything for a while, so I'll end the call. Goodbye.";
      addTurn(call.id, "assistant", bye);
      updateCall(call.id, { status: "no-response", endedAt: new Date().toISOString() });
      return speakAndHangup(bye);
    }
    return listenOnly(call.id);
  }

  call.emptyCount = 0;
  addTurn(call.id, "user", speech);

  let act;
  try {
    act = await generateAction(call);
  } catch (err) {
    console.error("generateAction failed:", err);
    const bye = "I'm sorry, I'm having a technical problem on my end. I'll follow up later. Goodbye.";
    addTurn(call.id, "assistant", bye);
    updateCall(call.id, { status: "error", error: String(err?.message || err), endedAt: new Date().toISOString() });
    return speakAndHangup(bye);
  }

  if (act.note) updateCall(call.id, { result: act.note });

  if (act.action === "hangup") {
    const bye = act.say || "Thank you. Goodbye.";
    addTurn(call.id, "assistant", bye);
    updateCall(call.id, { status: "completed", endedAt: new Date().toISOString() });
    return speakAndHangup(bye);
  }

  if (act.action === "press" && act.digits) {
    addTurn(call.id, "assistant", describePress(act.digits, call.accountNumber));
    return pressAndListen(call.id, act.digits);
  }

  // "speak" (or "press" with no valid digits — fall back to speaking).
  const words = act.say || "Sorry, could you repeat that?";
  addTurn(call.id, "assistant", words);
  return listen(call.id, words);
}

// The other end spoke — decide the next action and respond with TwiML.
app.post("/voice/respond", validateTwilio, async (req, res) => {
  const call = getCall(req.query.callId);
  if (!call) {
    return sendTwiml(res, plainHangup("Sorry, this call has ended. Goodbye."));
  }
  const speech = String(req.body.SpeechResult || "").trim();
  const vr = await handleTurn(call, speech);
  sendTwiml(res, vr);
});

// Lifecycle updates from Twilio (ringing, answered, completed, failed, ...).
app.post("/voice/status", validateTwilio, (req, res) => {
  const call = getCall(req.query.callId);
  if (call) {
    const twilioStatus = req.body.CallStatus;
    const patch = { twilioStatus };
    const terminal = ["completed", "busy", "failed", "no-answer", "canceled"];
    if (terminal.includes(twilioStatus)) {
      patch.endedAt = call.endedAt || new Date().toISOString();
      // Only let Twilio set a failure status if the app hasn't reached its own
      // terminal conversation state (completed / voicemail / no-response).
      const appTerminal = ["completed", "voicemail", "no-response", "error"];
      if (twilioStatus !== "completed" && !appTerminal.includes(call.status)) {
        patch.status = twilioStatus;
      }
    }
    updateCall(call.id, patch);
  }
  res.sendStatus(204);
});

app.listen(config.port, () => {
  const missing = missingConfig();
  console.log(`AI Caller running on http://localhost:${config.port}`);
  if (missing.length) {
    console.log(`⚠  Not fully configured yet. Set: ${missing.join(", ")} (see .env.example)`);
  } else {
    console.log(`✓ Configured. Public URL: ${config.publicUrl}`);
  }
});
