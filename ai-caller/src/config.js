import "dotenv/config";

export const config = {
  port: parseInt(process.env.PORT || "3000", 10),
  // Externally reachable base URL of this server (no trailing slash).
  publicUrl: (process.env.PUBLIC_URL || "").replace(/\/+$/, ""),

  anthropicApiKey: process.env.ANTHROPIC_API_KEY || "",
  model: process.env.ANTHROPIC_MODEL || "claude-opus-4-8",

  twilio: {
    accountSid: process.env.TWILIO_ACCOUNT_SID || "",
    authToken: process.env.TWILIO_AUTH_TOKEN || "",
    // A Twilio number on the account (optional if CALLER_ID is a verified number).
    fromNumber: process.env.TWILIO_FROM_NUMBER || "",
  },

  // The number the recipient sees, and the number the call is placed from.
  // Set this to your Telus VoIP number (verified in Twilio as an outgoing
  // caller ID — see `npm run verify:callerid`). Falls back to the Twilio number.
  callerId: process.env.CALLER_ID || process.env.TWILIO_FROM_NUMBER || "",

  voice: process.env.TWILIO_VOICE || "Polly.Joanna",
  callerName: process.env.CALLER_NAME || "",
  validateSignature: process.env.VALIDATE_TWILIO_SIGNATURE !== "false",
};

// Returns the list of required settings that are not yet present.
export function missingConfig() {
  const missing = [];
  if (!config.anthropicApiKey) missing.push("ANTHROPIC_API_KEY");
  if (!config.twilio.accountSid) missing.push("TWILIO_ACCOUNT_SID");
  if (!config.twilio.authToken) missing.push("TWILIO_AUTH_TOKEN");
  // Need a number to place the call from: your verified Telus number via
  // CALLER_ID, or a Twilio number via TWILIO_FROM_NUMBER.
  if (!config.callerId) missing.push("CALLER_ID (or TWILIO_FROM_NUMBER)");
  if (!config.publicUrl) missing.push("PUBLIC_URL");
  return missing;
}
