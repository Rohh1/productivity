// One-time: verify your Telus VoIP number as an outgoing caller ID in Twilio,
// so the AI caller can show it as the number people see.
//
//   cd ai-caller && npm run verify:callerid
//
// It reads the number from CALLER_ID in .env (or pass one as an argument):
//   node verify-callerid.mjs +15875551234
//
// Twilio will CALL that number. Answer it and, when prompted, key in the
// 6-digit code this script prints. After that, set CALLER_ID to this number.

import twilio from "twilio";
import { config } from "./src/config.js";

const number = (process.argv[2] || config.callerId || "").trim();

if (!config.twilio.accountSid || !config.twilio.authToken) {
  console.error("Set TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN in .env first.");
  process.exit(1);
}
if (!/^\+?[1-9]\d{6,14}$/.test(number.replace(/[\s().\-]/g, ""))) {
  console.error("Provide the number to verify in E.164 format, e.g. +15875551234");
  console.error("Either set CALLER_ID in .env or pass it: node verify-callerid.mjs +15875551234");
  process.exit(1);
}

const client = twilio(config.twilio.accountSid, config.twilio.authToken);

try {
  const req = await client.validationRequests.create({
    friendlyName: "AI Caller (Telus)",
    phoneNumber: number.replace(/[\s().\-]/g, ""),
  });
  console.log(`\nTwilio is now calling ${req.phoneNumber}.`);
  console.log("Answer it and enter this code on the keypad when asked:\n");
  console.log(`    ${req.validationCode}\n`);
  console.log("Once it says the number is verified, set CALLER_ID to this number in .env.");
} catch (err) {
  console.error("\nCouldn't start verification:", err?.message || err);
  if (String(err?.code) === "21450" || /already/i.test(err?.message || "")) {
    console.error("This number may already be verified — try setting CALLER_ID and placing a call.");
  }
  process.exit(1);
}
