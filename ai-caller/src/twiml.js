import { config } from "./config.js";
import { VoiceResponse } from "./twilio.js";

// Speak `text`, then listen for the callee's reply, which Twilio POSTs to
// /voice/respond. Used for every turn where the conversation continues.
export function listen(callId, text) {
  const vr = new VoiceResponse();
  const gather = vr.gather({
    input: "speech",
    action: `/voice/respond?callId=${callId}`,
    method: "POST",
    speechTimeout: "auto",
    speechModel: "phone_call",
    language: "en-US",
  });
  gather.say({ voice: config.voice }, text);
  return vr;
}

// Speak `text` in the configured voice, then hang up. Used to end the call.
export function speakAndHangup(text) {
  const vr = new VoiceResponse();
  vr.say({ voice: config.voice }, text);
  vr.hangup();
  return vr;
}

// Speak `text` in the default voice, then hang up. Used for error/edge cases
// where the call context is missing.
export function plainHangup(text) {
  const vr = new VoiceResponse();
  vr.say(text);
  vr.hangup();
  return vr;
}
