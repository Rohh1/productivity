import Anthropic from "@anthropic-ai/sdk";
import { config } from "./config.js";
import { sanitizeDigits } from "./util.js";

let _client = null;
function client() {
  if (!_client) _client = new Anthropic({ apiKey: config.anthropicApiKey });
  return _client;
}

// Each turn, the model must pick exactly one action.
const ACTION_SCHEMA = {
  type: "object",
  properties: {
    action: {
      type: "string",
      enum: ["speak", "press", "hangup"],
      description: "speak = say words out loud; press = press keypad digits; hangup = end the call.",
    },
    say: {
      type: "string",
      description: "The exact words to speak. Used for 'speak' and 'hangup'. Empty string for 'press'.",
    },
    digits: {
      type: "string",
      description:
        "Keypad digits to press for 'press'. Only 0-9, * , # and 'w' (a short pause). Empty string otherwise.",
    },
    note: {
      type: "string",
      description:
        "A short note for the operator's log of what you learned or did this turn (e.g. the balance or status). Never put the account number here. Empty string if nothing to note.",
    },
  },
  required: ["action", "say", "digits", "note"],
  additionalProperties: false,
};

function systemPrompt(call) {
  const on = call.callerName || "the person I'm assisting";
  const target = call.calleeName || "a support line";
  const hasAccount = Boolean(call.accountNumber);
  return `You are ${call.persona || "a calm, efficient personal assistant"} on a live phone call, placed on behalf of ${on}. You are calling ${target}. The other end may be an automated phone menu (IVR) or a human agent — adapt to whichever you get.

Your goal for this call:
${call.goal}

Each turn you are given a transcription of what the other end just said. Respond by choosing exactly ONE action:
- "speak": say words out loud (to a human, or to a voice-response system). Put the words in "say".
- "press": press keypad digits — to choose a menu option, or to enter a number when prompted. Put the digits in "digits" (0-9, * , # ; add "w" for a short pause, and end with "#" if they ask for pound/hash).
- "hangup": end the call. Put a short spoken goodbye in "say".
${hasAccount ? `\nInformation you may enter when the system explicitly asks for it:\n- Account number: ${call.accountNumber}\n` : ""}
Rules:
- Move deliberately toward the goal. If you hear a menu ("press 1 for billing…"), "press" the option that best fits the goal.
${hasAccount ? `- When prompted for the account number, "press" it (append "#" if they ask for pound). Only enter it when explicitly asked. Never say the account number out loud and never write it in "note".\n` : ""}- Keep any spoken words to one or two short, natural sentences. No markdown, no lists, no emoji.
- Never invent account details, confirmation numbers, or facts you weren't given.
- When you've obtained what the goal asks for, or the call clearly can't proceed, choose "hangup" and record the key result in "note".
- Use "note" to capture the answer you were calling to get (a balance, a status, a yes/no) so it can be logged. Keep it short.`;
}

function textOf(response) {
  return (response.content || [])
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join(" ")
    .trim();
}

// The synthetic leading user turn guarantees the array starts with a user
// message (an API requirement) and marks the moment the call connected.
export function toMessages(call) {
  const messages = [{ role: "user", content: "(The call has just connected.)" }];
  for (const turn of call.turns) {
    messages.push({
      role: turn.role === "assistant" ? "assistant" : "user",
      content: turn.text,
    });
  }
  return messages;
}

// Turn the model's JSON into a normalized, validated action.
export function parseAction(raw) {
  let obj;
  try {
    obj = JSON.parse(raw);
  } catch {
    obj = { action: "speak", say: raw, digits: "", note: "" };
  }
  const action = ["speak", "press", "hangup"].includes(obj.action) ? obj.action : "speak";
  return {
    action,
    say: String(obj.say || "").trim(),
    digits: sanitizeDigits(obj.digits),
    note: String(obj.note || "").trim(),
  };
}

// Optional spoken opening, used only when the call should speak first
// (e.g. reaching a human receptionist rather than an IVR).
export async function generateOpening(call) {
  const response = await client().messages.create({
    model: config.model,
    max_tokens: 150,
    thinking: { type: "disabled" },
    output_config: { effort: "low" },
    system: systemPrompt(call),
    messages: [
      {
        role: "user",
        content:
          "The call just connected and a person said hello. Give your first spoken line: greet them briefly and start toward your goal. One or two sentences. Reply with only the words to say.",
      },
    ],
  });
  return textOf(response).trim();
}

// Given the conversation so far, decide the next action.
export async function generateAction(call) {
  const response = await client().messages.create({
    model: config.model,
    max_tokens: 300,
    thinking: { type: "disabled" },
    output_config: {
      effort: "low",
      format: { type: "json_schema", schema: ACTION_SCHEMA },
    },
    system: systemPrompt(call),
    messages: toMessages(call),
  });
  return parseAction(textOf(response));
}
