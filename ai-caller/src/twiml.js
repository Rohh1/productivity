import { config } from "./config.js";
import { VoiceResponse } from "./twilio.js";

// Attach a <Gather> that transcribes what the other end says and POSTs the
// result to /voice/respond. `timeout` gives an IVR a moment to start talking.
function addGather(vr, callId, spokenText) {
  const gather = vr.gather({
    input: "speech",
    action: `/voice/respond?callId=${callId}`,
    method: "POST",
    speechTimeout: "auto",
    speechModel: "phone_call",
    language: "en-US",
    timeout: 8,
  });
  if (spokenText) gather.say({ voice: config.voice }, spokenText);
}

// Speak `text`, then listen for the reply.
export function listen(callId, text) {
  const vr = new VoiceResponse();
  addGather(vr, callId, text);
  return vr;
}

// Listen without saying anything first (for IVRs that speak first).
export function listenOnly(callId) {
  const vr = new VoiceResponse();
  addGather(vr, callId);
  return vr;
}

// Press keypad digits (DTMF tones), then listen for what the system says next.
export function pressAndListen(callId, digits) {
  const vr = new VoiceResponse();
  if (digits) vr.play({ digits });
  addGather(vr, callId);
  return vr;
}

// Speak `text` in the configured voice, then hang up.
export function speakAndHangup(text) {
  const vr = new VoiceResponse();
  vr.say({ voice: config.voice }, text);
  vr.hangup();
  return vr;
}

// Speak `text` in the default voice, then hang up (edge/error cases).
export function plainHangup(text) {
  const vr = new VoiceResponse();
  vr.say(text);
  vr.hangup();
  return vr;
}
