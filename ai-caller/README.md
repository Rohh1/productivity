# AI Caller

An AI voice agent that places **real outbound phone calls** and holds a live
conversation to accomplish a goal you give it — "book a table for 4 tonight",
"ask the pharmacy if my prescription is ready", or navigate a **support line's
automated menu, punch in your account number, and read back the answer**.

Twilio handles the phone line and speech ⇄ text. Claude (`claude-opus-4-8`) is
the brain: each turn it decides whether to **speak**, **press keypad keys**
(DTMF — for menus and entering numbers), or **hang up**.

```
Browser UI  ──►  Node/Express server  ──►  Twilio (places the call, TTS + STT + DTMF)
                        │                        │
                        └────► Claude ◄──────────┘   each time the other end speaks,
                          picks the next action:
                          speak · press keys · hang up
```

## What you need

1. **Node.js 18+**
2. **An Anthropic API key** — <https://console.anthropic.com>
3. **A Twilio account + a phone number** with Voice enabled —
   <https://www.twilio.com/console> (Account SID, Auth Token, and the number)
4. **A public URL** for this server so Twilio can call back to it. Locally,
   use [ngrok](https://ngrok.com): `ngrok http 3000`.

## Setup

```bash
cd ai-caller
npm install
cp .env.example .env
# edit .env and fill in your keys, Twilio number, and PUBLIC_URL
npm start
```

Open <http://localhost:3000>, enter a phone number and a goal, and click
**Place call**. The transcript streams into the page live as the call happens.

## Calling a support line (IVR + keypad)

For "call the support line, enter my account number, and get X" tasks:

1. Fill in the **goal** (e.g. "Get my current balance and whether autopay is on").
2. Put your **account number** in the *Account number / digits to enter* field.
3. Leave **"Support line / automated menu"** checked (default). The AI then
   *listens first*, presses menu options to reach the goal, and enters your
   account number as keypad tones when the line asks for it.
4. When it has the answer, it hangs up. The **Result** it captured (the balance,
   status, etc.) shows at the top of that call's card.

**Your account number stays on your own server.** It's sent to the phone line
only as DTMF tones when prompted, is never spoken aloud, is masked in the
transcript ("Entered account number"), and is never returned by the API.

Uncheck the box when you're reaching a *human* (receptionist, restaurant) so the
AI speaks first instead of waiting.

## Test it without a phone (simulation)

You can watch the AI navigate a **fake** support-line menu and enter a fake
account number end-to-end — no Twilio, no phone, just an Anthropic key:

```bash
# Terminal 1 — only ANTHROPIC_API_KEY is required for this
npm start
# Terminal 2
npm run simulate
```

Example output:

```
☎  IVR: For English, press 1.
🤖 AI:  Pressed 1
☎  IVR: For your account balance, press 2. ...
🤖 AI:  Pressed 2
☎  IVR: Please enter your account number followed by the pound sign.
🤖 AI:  Entered account number
☎  IVR: Your current balance is 152 dollars and 30 cents. Autopay is on. ...
🤖 AI:  Great, thank you. Goodbye.

■ Result the AI got back: Balance is $152.30; autopay is on.
```

Edit the `scenario` at the top of `simulate.mjs` to try your own menu script.

### Configuration (`.env`)

| Variable | Required | Description |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | yes | Your Anthropic API key |
| `TWILIO_ACCOUNT_SID` | yes | Twilio Account SID |
| `TWILIO_AUTH_TOKEN` | yes | Twilio Auth Token |
| `TWILIO_FROM_NUMBER` | yes | A Twilio number you own (E.164, e.g. `+14155550100`) |
| `PUBLIC_URL` | yes | Public https base URL of this server (e.g. your ngrok URL), no trailing slash |
| `ANTHROPIC_MODEL` | no | Defaults to `claude-opus-4-8` |
| `CALLER_NAME` | no | The name the AI says it's calling on behalf of |
| `TWILIO_VOICE` | no | Twilio TTS voice, defaults to `Polly.Joanna` |
| `PORT` | no | Defaults to `3000` |
| `VALIDATE_TWILIO_SIGNATURE` | no | `true` (default) verifies webhooks are really from Twilio |

## How a call flows

1. You submit a number + goal (+ optional account number) → `POST /api/calls`.
2. The server tells Twilio to dial.
3. When the line answers, Twilio fetches TwiML from `/voice/answer`. For a
   support line the server just listens; for a human it speaks an opening first.
4. Each time the other end speaks, Twilio POSTs the transcription to
   `/voice/respond`. The server sends the conversation to Claude, which returns
   one action — **speak**, **press** digits, or **hang up** — and the server
   renders the matching TwiML (`<Say>`, `<Play digits>`, or `<Hangup>`).
5. When the goal is met, Claude chooses **hang up** and records what it learned;
   the server says goodbye and hangs up.
6. `/voice/status` records Twilio lifecycle events; the browser polls
   `/api/calls/:id` to render the live transcript, keypad presses, and result.

## Project layout

```
ai-caller/
├── server.js            Express app: browser API + Twilio voice webhooks
├── simulate.mjs         Drive the AI through a fake IVR (no phone needed)
├── src/
│   ├── config.js        Env config + "is it configured?" check
│   ├── calls.js         In-memory call + transcript store (+ account privacy)
│   ├── anthropic.js     Claude engine — picks speak / press / hangup each turn
│   ├── twiml.js         TwiML builders (say, play DTMF digits, gather, hangup)
│   ├── twilio.js        Place calls, verify webhook signatures
│   └── util.js          Digit sanitizing + privacy-safe keypad descriptions
└── public/              The web UI (index.html, styles.css, app.js)
```

## Notes & limits

- **In-memory store.** Calls and transcripts live in the process; a restart
  clears them. Swap `src/calls.js` for a database to persist.
- **Turn-based, not full-duplex.** The AI speaks/presses, then listens — it
  doesn't talk over the other end. For ultra-low-latency, barge-in
  conversations you'd move to Twilio Media Streams with a streaming STT/TTS
  pipeline.
- **IVR reading is best-effort.** The AI "hears" a menu via Twilio's speech
  recognition of the line audio, then presses a key. Fast menus, heavy accents,
  or "say your request" prompts can trip it up. Give a clear goal, and a
  descriptive account/menu hint helps. Test against your target line.
- **Costs.** You pay Twilio per minute + per speech-recognition, and Anthropic
  per token. Replies are kept to 1–2 sentences to stay fast and cheap.

## Please use this responsibly

Only call people who expect your call or have consented to it. Many places
require you to disclose that a call is recorded or AI-driven, and outright ban
unsolicited automated calls. You are responsible for complying with the
robocall, consent, and recording laws that apply to you and the person you're
calling. Don't use this for spam, mass calling, or to deceive anyone about who
or what they're talking to.
