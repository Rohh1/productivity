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
    fromNumber: process.env.TWILIO_FROM_NUMBER || "",
  },

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
  if (!config.twilio.fromNumber) missing.push("TWILIO_FROM_NUMBER");
  if (!config.publicUrl) missing.push("PUBLIC_URL");
  return missing;
}
