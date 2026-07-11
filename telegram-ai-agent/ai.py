"""Claude integration: draft replies to customers and learn the worker's style."""

import json
import os
import re

from anthropic import AsyncAnthropic

MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-8")

client = AsyncAnthropic()  # reads ANTHROPIC_API_KEY from the environment

BASE_INSTRUCTIONS = """\
You draft Telegram replies for "Gold Club", an IPTV subscription service. \
Incoming chats are either SALES (pricing, plans, trials, how to buy) or \
SUPPORT (setup help, buffering, channels not working, login/renewal issues).

Rules:
- A human support worker reviews every draft before it is sent, but write it \
ready-to-send: no placeholders, no "[insert price]", no meta-commentary.
- Only state facts (prices, plans, policies, setup steps) that appear in the \
business knowledge section. If the knowledge doesn't cover it, ask a short \
clarifying question or say the team will confirm — never invent details.
- Match the customer's language. If they write in another language, reply in it.
- Keep it Telegram-sized: short, direct, friendly. No corporate boilerplate.
- If a learned style guide is provided, imitate it closely — greetings, \
sign-offs, emoji habits, message length, phrasing. The past example replies \
show exactly how the worker likes messages written; treat them as ground truth \
for tone and format.
"""

DRAFT_FORMAT = {
    "type": "json_schema",
    "schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string", "enum": ["sales", "support", "other"]},
            "reply": {"type": "string"},
        },
        "required": ["category", "reply"],
        "additionalProperties": False,
    },
}

_WORD_RE = re.compile(r"[a-zà-ÿ0-9]+", re.IGNORECASE)
_STOPWORDS = {
    "the", "a", "an", "is", "it", "i", "my", "me", "to", "for", "and", "or",
    "on", "in", "of", "do", "you", "can", "how", "what", "with", "have", "not",
    "this", "that", "your", "please", "hi", "hello", "hey",
}


def _words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS}


def pick_examples(message: str, examples: list, k: int = 4) -> list:
    """Rank past approved/edited exchanges by word overlap with the new message."""
    target = _words(message)
    scored = []
    for ex in examples:
        overlap = len(target & _words(ex["customer_message"]))
        if overlap > 0:
            scored.append((overlap, ex))
    scored.sort(key=lambda t: t[0], reverse=True)
    picked = [ex for _, ex in scored[:k]]
    # Pad with the most recent examples so early drafts still get style signal.
    for ex in examples:
        if len(picked) >= k:
            break
        if ex not in picked:
            picked.append(ex)
    return picked


def _build_system(knowledge: str, style_guide: str) -> list[dict]:
    # Stable content first, with cache breakpoints, so the prefix caches across
    # messages (see prompt-caching guidance: prefix match, stable-before-volatile).
    system = [
        {
            "type": "text",
            "text": BASE_INSTRUCTIONS + "\n\n# Business knowledge\n" + knowledge,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    if style_guide.strip():
        system.append(
            {
                "type": "text",
                "text": "# Learned style guide (how our worker writes)\n" + style_guide,
                "cache_control": {"type": "ephemeral"},
            }
        )
    return system


async def draft_reply(customer_message: str, history: list, examples: list,
                      knowledge: str, style_guide: str) -> tuple[str, str]:
    """Return (category, reply) for a new customer message."""
    parts = []

    picked = pick_examples(customer_message, examples)
    if picked:
        blocks = []
        for ex in picked:
            blocks.append(
                f"Customer: {ex['customer_message']}\n"
                f"Our reply ({ex['category']}): {ex['final_reply']}"
            )
        parts.append(
            "# Past exchanges answered by our worker (imitate these)\n\n"
            + "\n\n---\n\n".join(blocks)
        )

    if history:
        lines = [
            ("Customer: " if r["role"] == "customer" else "Us: ") + r["text"]
            for r in history
        ]
        parts.append("# Current conversation so far\n" + "\n".join(lines))

    parts.append(
        "# New customer message\n" + customer_message
        + "\n\nClassify it and draft the reply."
    )

    response = await client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        output_config={"effort": "medium", "format": DRAFT_FORMAT},
        system=_build_system(knowledge, style_guide),
        messages=[{"role": "user", "content": "\n\n".join(parts)}],
    )
    if response.stop_reason == "refusal":
        return "other", ""
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    return data["category"], data["reply"].strip()


async def learn_style(edit_pairs: list, approved: list, current_guide: str) -> str:
    """Distill the worker's edits into an updated style guide."""
    sections = []

    if current_guide.strip():
        sections.append("# Current style guide (to update)\n" + current_guide)

    if edit_pairs:
        blocks = []
        for p in edit_pairs:
            blocks.append(
                f"Customer: {p['customer_message']}\n"
                f"AI draft: {p['ai_draft']}\n"
                f"Worker's final version: {p['final_reply']}"
            )
        sections.append(
            "# Drafts the worker EDITED before sending (draft vs final)\n\n"
            + "\n\n---\n\n".join(blocks)
        )

    if approved:
        blocks = [
            f"Customer: {p['customer_message']}\nSent as-is: {p['final_reply']}"
            for p in approved
        ]
        sections.append(
            "# Drafts the worker approved unchanged\n\n" + "\n\n---\n\n".join(blocks)
        )

    sections.append(
        "Write an updated style guide for the AI that drafts these replies. "
        "Focus on what the worker's edits reveal: tone, greetings and sign-offs, "
        "emoji usage, message length, wording they prefer or remove, facts they "
        "correct, and anything they consistently add. Merge with the current "
        "guide, keep what still holds, drop what the new evidence contradicts. "
        "Concise bullet points, under 350 words. Output ONLY the guide text."
    )

    response = await client.messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": "\n\n".join(sections)}],
    )
    if response.stop_reason == "refusal":
        return current_guide
    return next(b.text for b in response.content if b.type == "text").strip()
