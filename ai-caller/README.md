# AI Caller

An AI voice agent that places **real outbound phone calls** and holds a live
conversation to accomplish a goal you give it — "book a table for 4 tonight",
"ask the pharmacy if my prescription is ready", "confirm the appointment".

Twilio handles the phone line and speech ⇄ text. Claude
(`claude-opus-4-8`) is the brain that decides what to say next.

```
Browser UI  ──►  Node/Express server  ──►  Twilio (places the call, TTS + STT)
                        │                        │
                        └────► Claude ◄──────────┘   (each time the callee speaks,
                          decides the next spoken line)
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

1. You submit a number + goal in the UI → `POST /api/calls`.
2. The server asks Claude for an opening line, then tells Twilio to dial.
3. When the callee answers, Twilio fetches TwiML from `/voice/answer`; the
   server speaks the opening and starts listening (`<Gather input="speech">`).
4. Each time the callee speaks, Twilio POSTs the transcription to
   `/voice/respond`; the server sends the conversation to Claude, speaks the
   reply, and listens again.
5. When the goal is met (Claude appends a private `[[END_CALL]]` marker) or the
   line goes quiet, the server says goodbye and hangs up.
6. `/voice/status` records Twilio lifecycle events; the browser polls
   `/api/calls/:id` to render the live transcript and status.

## Project layout

```
ai-caller/
├── server.js            Express app: browser API + Twilio voice webhooks
├── src/
│   ├── config.js        Env config + "is it configured?" check
│   ├── calls.js         In-memory call + transcript store
│   ├── anthropic.js     Claude conversation engine (opening + replies)
│   └── twilio.js        Place calls, build TwiML, verify webhook signatures
└── public/              The web UI (index.html, styles.css, app.js)
```

## Notes & limits

- **In-memory store.** Calls and transcripts live in the process; a restart
  clears them. Swap `src/calls.js` for a database to persist.
- **Turn-based, not full-duplex.** The AI speaks, then listens — it doesn't
  talk over the other person. This keeps it simple and reliable. For
  ultra-low-latency, barge-in conversations you'd move to Twilio Media Streams
  with a streaming STT/TTS pipeline.
- **Costs.** You pay Twilio per minute + per speech-recognition, and Anthropic
  per token. Replies are kept to 1–2 sentences to stay fast and cheap.

## Please use this responsibly

Only call people who expect your call or have consented to it. Many places
require you to disclose that a call is recorded or AI-driven, and outright ban
unsolicited automated calls. You are responsible for complying with the
robocall, consent, and recording laws that apply to you and the person you're
calling. Don't use this for spam, mass calling, or to deceive anyone about who
or what they're talking to.
