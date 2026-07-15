import twilio from "twilio";
import { config } from "./config.js";

let _client = null;
export function twilioClient() {
  if (!_client) {
    _client = twilio(config.twilio.accountSid, config.twilio.authToken);
  }
  return _client;
}

// Kick off an outbound call. Twilio will fetch TwiML from /voice/answer once
// the callee picks up, and POST lifecycle updates to /voice/status.
export async function placeCall(call) {
  const created = await twilioClient().calls.create({
    to: call.to,
    from: config.twilio.fromNumber,
    url: `${config.publicUrl}/voice/answer?callId=${call.id}`,
    method: "POST",
    statusCallback: `${config.publicUrl}/voice/status?callId=${call.id}`,
    statusCallbackMethod: "POST",
    statusCallbackEvent: ["initiated", "ringing", "answered", "completed"],
    machineDetection: "Enable",
  });
  return created.sid;
}

// Express middleware: reject webhook requests that aren't signed by Twilio.
export function validateTwilio(req, res, next) {
  if (!config.validateSignature) return next();
  const signature = req.headers["x-twilio-signature"];
  const url = `${config.publicUrl}${req.originalUrl}`;
  const valid = twilio.validateRequest(config.twilio.authToken, signature, url, req.body || {});
  if (!valid) {
    console.warn("Rejected webhook with invalid Twilio signature:", url);
    return res.status(403).type("text/plain").send("Invalid Twilio signature");
  }
  next();
}

export const VoiceResponse = twilio.twiml.VoiceResponse;
