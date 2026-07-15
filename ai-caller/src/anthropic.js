import Anthropic from "@anthropic-ai/sdk";
import { config } from "./config.js";

// Sentinel the model appends to its final line when the call should end.
// It is stripped before anything is spoken.
const END_TOKEN = "[[END_CALL]]";

let _client = null;
function client() {
  if (!_client) _client = new Anthropic({ apiKey: config.anthropicApiKey });
  return _client;
}

function systemPrompt(call) {
  const on = call.callerName || "the person I'm assisting";
  const who = call.calleeName || "the person who answered";
  return `You are ${call.persona || "a friendly, professional personal assistant"} making a phone call on behalf of ${on}.
You are speaking OUT LOUD on a live phone call to ${who}. Everything you write is converted to speech and played to them, so write only words a person would say.

Your goal for this call:
${call.goal}

How to speak:
- Keep every reply to one or two short sentences. This is real-time voice, not chat.
- Sound natural and human. No markdown, no bullet points, no emoji, no stage directions, no quotation marks around your words.
- Ask one thing at a time, then wait for their answer.
- If they ask who you are, be honest: you are an AI assistant calling on behalf of ${on}.
- Never invent facts, prices, names, or commitments you were not given. If you don't know something, say you'll follow up.
- Do not spell out numbers as digits awkwardly; speak them the way a person would.

Ending the call:
- When the goal is achieved, or the other person wants to hang up, or the call clearly cannot proceed, say a brief, polite goodbye and then append ${END_TOKEN} at the very end of that final message.
- Only ever include ${END_TOKEN} when you are genuinely finished. Never say the token out loud and never mention it.

Respond with ONLY the exact words you will speak next.`;
}

function textOf(response) {
  return (response.content || [])
    .filter((b) => b.type === "text")
    .map((b) => b.text)
    .join(" ")
    .trim();
}

// Split the model's raw output into the words to speak and whether the call
// should end now. Exported so the parsing rule can be tested without the API.
export function parseReply(raw) {
  const text = (raw || "").trim();
  const done = text.includes(END_TOKEN);
  const spoken = text.replaceAll(END_TOKEN, "").trim();
  return { spoken, done };
}

// Build the Messages API history from stored turns. A synthetic opening
// user turn guarantees the array starts with a user message (API requirement)
// and represents the moment the call connected.
export function toMessages(call) {
  const messages = [{ role: "user", content: "(The call has just connected and the person answered.)" }];
  for (const turn of call.turns) {
    messages.push({
      role: turn.role === "assistant" ? "assistant" : "user",
      content: turn.text,
    });
  }
  return messages;
}

// The first thing the AI says when the callee picks up. Precomputed at call
// creation so there is no model latency on pickup.
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
          "The call just connected and the person said hello. Give your very first spoken line: greet them and start moving toward your goal. One or two sentences.",
      },
    ],
  });
  return textOf(response).replaceAll(END_TOKEN, "").trim();
}

// Given the conversation so far (ending with the callee's latest words),
// produce the next spoken line and whether the call should now end.
export async function generateReply(call) {
  const response = await client().messages.create({
    model: config.model,
    max_tokens: 300,
    thinking: { type: "disabled" },
    output_config: { effort: "low" },
    system: systemPrompt(call),
    messages: toMessages(call),
  });
  const { spoken, done } = parseReply(textOf(response));
  return { spoken: spoken || "Thanks so much. Goodbye.", done };
}
