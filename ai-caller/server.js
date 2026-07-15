import express from "express";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

import { config, missingConfig } from "./src/config.js";
import { createCall, getCall, listCalls, addTurn, updateCall } from "./src/calls.js";
import { generateOpening, generateReply } from "./src/anthropic.js";
import { placeCall, validateTwilio } from "./src/twilio.js";
import { listen, speakAndHangup, plainHangup } from "./src/twiml.js";

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

app.get("/api/calls", (_req, res) => res.json({ calls: listCalls() }));

app.get("/api/calls/:id", (req, res) => {
  const call = getCall(req.params.id);
  if (!call) return res.status(404).json({ error: "Call not found" });
  res.json({ call });
});

app.post("/api/calls", async (req, res) => {
  const missing = missingConfig();
  if (missing.length) {
    return res.status(400).json({ error: `Server is not fully configured. Missing: ${missing.join(", ")}` });
  }

  const { to, goal, calleeName, persona } = req.body || {};
  const number = normalize(to);
  if (!PHONE_RE.test(number)) {
    return res.status(400).json({ error: "Enter a valid phone number in E.164 format, e.g. +14155550123." });
  }
  if (!goal || !String(goal).trim()) {
    return res.status(400).json({ error: "Describe the goal of the call." });
  }

  const call = createCall({
    to: number,
    goal: String(goal).trim(),
    calleeName: (calleeName || "").trim(),
    persona: (persona || "").trim(),
    callerName: config.callerName,
  });

  // Precompute the opening line so there's no AI latency when the callee answers.
  try {
    call.opening = await generateOpening(call);
  } catch (err) {
    console.error("Opening generation failed:", err);
    call.opening = `Hi${call.calleeName ? " " + call.calleeName : ""}, this is an assistant calling on behalf of ${config.callerName || "a client"}. Do you have a quick moment?`;
  }

  try {
    const sid = await placeCall(call);
    updateCall(call.id, { twilioSid: sid, status: "ringing" });
  } catch (err) {
    console.error("Failed to place call:", err);
    updateCall(call.id, { status: "error", error: String(err?.message || err) });
    return res.status(502).json({ error: `Could not place the call: ${err?.message || err}`, call });
  }

  res.status(201).json({ call });
});

// ---------------------------------------------------------------------------
// Twilio voice webhooks
// ---------------------------------------------------------------------------

// Callee picked up — say the opening line, then listen.
app.post("/voice/answer", validateTwilio, (req, res) => {
  const call = getCall(req.query.callId);
  if (!call) {
    return sendTwiml(res, plainHangup("Sorry, this call is no longer available. Goodbye."));
  }

  // Answering machine detected — leave the opening as a short message and hang up.
  if (String(req.body.AnsweredBy || "").startsWith("machine")) {
    updateCall(call.id, { status: "voicemail", endedAt: new Date().toISOString() });
    return sendTwiml(res, speakAndHangup(call.opening || "Sorry I missed you. I'll try again later. Goodbye."));
  }

  updateCall(call.id, { status: "in-progress" });
  const opening = call.opening || "Hello!";
  addTurn(call.id, "assistant", opening);
  sendTwiml(res, listen(call.id, opening));
});

// Callee spoke — feed it to Claude and say the reply (or hang up if done).
app.post("/voice/respond", validateTwilio, async (req, res) => {
  const call = getCall(req.query.callId);
  if (!call) {
    return sendTwiml(res, plainHangup("Sorry, this call has ended. Goodbye."));
  }

  const speech = String(req.body.SpeechResult || "").trim();

  // No speech captured — reprompt once, then give up gracefully.
  if (!speech) {
    call.emptyCount = (call.emptyCount || 0) + 1;
    if (call.emptyCount >= 2) {
      const bye = "I'm having trouble hearing you, so I'll try again later. Goodbye.";
      addTurn(call.id, "assistant", bye);
      updateCall(call.id, { status: "no-response", endedAt: new Date().toISOString() });
      return sendTwiml(res, speakAndHangup(bye));
    }
    const reprompt = "Sorry, I didn't catch that. Could you say that again?";
    addTurn(call.id, "assistant", reprompt);
    return sendTwiml(res, listen(call.id, reprompt));
  }

  call.emptyCount = 0;
  addTurn(call.id, "user", speech);

  let reply;
  try {
    reply = await generateReply(call);
  } catch (err) {
    console.error("generateReply failed:", err);
    const bye = "I'm sorry, I'm having a technical problem on my end. I'll follow up later. Goodbye.";
    addTurn(call.id, "assistant", bye);
    updateCall(call.id, { status: "error", error: String(err?.message || err), endedAt: new Date().toISOString() });
    return sendTwiml(res, speakAndHangup(bye));
  }

  addTurn(call.id, "assistant", reply.spoken);
  if (reply.done) {
    updateCall(call.id, { status: "completed", endedAt: new Date().toISOString() });
    return sendTwiml(res, speakAndHangup(reply.spoken));
  }
  sendTwiml(res, listen(call.id, reply.spoken));
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
