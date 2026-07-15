// Keep only real keypad symbols. Prevents anything odd from reaching TwiML,
// and caps length so a runaway value can't dial forever.
// Allowed: 0-9, * , # , and 'w' (Twilio = 0.5s pause).
export function sanitizeDigits(input) {
  return String(input || "").replace(/[^0-9*#w]/gi, "").slice(0, 40);
}

export function onlyDigits(input) {
  return String(input || "").replace(/\D/g, "");
}

// A privacy-preserving description of a keypad press for the transcript/log.
// Never reveals the full account number.
export function describePress(digits, accountNumber) {
  const acct = onlyDigits(accountNumber);
  const pressed = onlyDigits(digits);
  if (acct.length >= 3 && pressed.includes(acct)) {
    return "Entered account number";
  }
  const visible = String(digits).replace(/w/gi, "").trim();
  if (visible.replace(/[^0-9*#]/g, "").length <= 4) {
    return `Pressed ${visible}`;
  }
  return "Entered digits";
}
