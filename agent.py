#!/usr/bin/env python3
"""
Sales Confirmation Call Agent

Listens to a phone call transcript between a confirmer and a prospect,
extracts key appointment information using Claude Opus 4.7, and saves
it to a notepad file.

Usage:
    python agent.py

Then select one of three modes:
  1  Live mode       — enter turns one by one as the call happens
  2  Transcript mode — paste a full call transcript
  3  Demo mode       — run the built-in example
"""

import os
import sys
from datetime import datetime
from pathlib import Path

import anthropic

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

MODEL = "claude-opus-4-7"
NOTEPAD_FILE = "notepad.txt"

# System prompt is cached so repeated calls don't re-pay full input cost.
SYSTEM_PROMPT = """\
You are an expert sales confirmation call assistant for a sales company. \
Your job is to analyze phone call transcripts between a confirmer and a \
prospect, extract all key appointment and contact information, and save it \
using the save_to_notepad tool.

Always extract the following fields when present in the transcript:
- Prospect's full name
- Phone number and email address
- Appointment date and time (as stated in the call)
- Appointment location or address
- Product or service being discussed
- Confirmation status: "confirmed", "rescheduled", "cancelled", or "pending"
  • confirmed   = appointment is set and the prospect agreed
  • rescheduled = appointment moved to a new date/time
  • cancelled   = prospect cancelled; appointment will not happen
  • pending     = outcome unclear; needs follow-up
- Special notes, requirements, or concerns the prospect raised
- Follow-up actions required after the call (e.g. send confirmation email)
- Confirmer's name (if mentioned)

Rules:
- Always call save_to_notepad once you have analyzed the full transcript, \
even if some fields are missing.
- Omit fields that were not mentioned — do not guess.
- Capture every detail the prospect stated (name corrections, extra attendees, \
access instructions, preferred contact method, etc.).\
"""

# ---------------------------------------------------------------------------
# Tool definition
# ---------------------------------------------------------------------------

TOOLS = [
    {
        "name": "save_to_notepad",
        "description": (
            "Save extracted appointment information from a confirmation call "
            "to the notepad file. Call this exactly once after analyzing the "
            "complete transcript."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "prospect_name": {
                    "type": "string",
                    "description": "Full name of the prospect",
                },
                "phone_number": {
                    "type": "string",
                    "description": "Prospect's phone number",
                },
                "email": {
                    "type": "string",
                    "description": "Prospect's email address",
                },
                "appointment_date": {
                    "type": "string",
                    "description": "Date of the appointment as stated in the call",
                },
                "appointment_time": {
                    "type": "string",
                    "description": "Time of the appointment as stated in the call",
                },
                "appointment_location": {
                    "type": "string",
                    "description": "Location or address for the appointment",
                },
                "product_or_service": {
                    "type": "string",
                    "description": "Product or service the appointment is about",
                },
                "confirmation_status": {
                    "type": "string",
                    "enum": ["confirmed", "rescheduled", "cancelled", "pending"],
                    "description": "Outcome / status of the appointment",
                },
                "special_notes": {
                    "type": "string",
                    "description": (
                        "Special requirements, concerns, or notes raised by "
                        "the prospect during the call"
                    ),
                },
                "follow_up_actions": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of follow-up actions needed after this call",
                },
                "confirmer_name": {
                    "type": "string",
                    "description": "Name of the confirmer who conducted the call",
                },
            },
            "required": ["prospect_name", "confirmation_status"],
        },
    }
]

# ---------------------------------------------------------------------------
# Notepad writer
# ---------------------------------------------------------------------------

_STATUS_LABEL = {
    "confirmed": "✓  CONFIRMED",
    "rescheduled": "↺  RESCHEDULED",
    "cancelled": "✗  CANCELLED",
    "pending": "?  PENDING",
}


def _wrap(text: str, width: int = 56, indent: str = "  ") -> list[str]:
    """Simple word-wrap returning a list of lines."""
    words = text.split()
    lines: list[str] = []
    current = indent
    for word in words:
        if len(current) + len(word) + 1 > width and current.strip():
            lines.append(current.rstrip())
            current = indent + word + " "
        else:
            current += word + " "
    if current.strip():
        lines.append(current.rstrip())
    return lines


def save_to_notepad(data: dict) -> str:
    """Append a formatted call record to the notepad file and return the path."""
    notepad_path = Path(NOTEPAD_FILE)
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status_raw = data.get("confirmation_status", "pending")
    status_label = _STATUS_LABEL.get(status_raw, status_raw.upper())

    def field(label: str, key: str) -> str | None:
        val = data.get(key)
        return f"  {label:<24}{val}" if val else None

    rows: list[str] = []

    # ── Header ────────────────────────────────────────────────────────────
    rows += [
        "=" * 62,
        f"  CALL RECORD   {timestamp}",
        "=" * 62,
        "",
    ]

    # ── Contact info ──────────────────────────────────────────────────────
    rows.append("  PROSPECT CONTACT")
    rows.append("  " + "─" * 40)
    for label, key in [
        ("Name:", "prospect_name"),
        ("Phone:", "phone_number"),
        ("Email:", "email"),
    ]:
        line = field(label, key)
        if line:
            rows.append(line)
    rows.append("")

    # ── Appointment details ───────────────────────────────────────────────
    rows.append("  APPOINTMENT DETAILS")
    rows.append("  " + "─" * 40)
    for label, key in [
        ("Date:", "appointment_date"),
        ("Time:", "appointment_time"),
        ("Location:", "appointment_location"),
        ("Product / Service:", "product_or_service"),
    ]:
        line = field(label, key)
        if line:
            rows.append(line)
    rows.append(f"  {'Status:':<24}{status_label}")
    rows.append("")

    # ── Notes ─────────────────────────────────────────────────────────────
    notes = data.get("special_notes", "").strip()
    if notes:
        rows.append("  NOTES")
        rows.append("  " + "─" * 40)
        rows.extend(_wrap(notes))
        rows.append("")

    # ── Follow-up actions ─────────────────────────────────────────────────
    follow_ups: list[str] = data.get("follow_up_actions") or []
    if follow_ups:
        rows.append("  FOLLOW-UP ACTIONS")
        rows.append("  " + "─" * 40)
        for action in follow_ups:
            rows.append(f"  •  {action}")
        rows.append("")

    # ── Confirmer ─────────────────────────────────────────────────────────
    confirmer = data.get("confirmer_name", "").strip()
    if confirmer:
        rows.append(f"  Confirmer: {confirmer}")
        rows.append("")

    rows.append("")  # trailing blank line between records

    entry = "\n".join(rows)
    with open(notepad_path, "a", encoding="utf-8") as fh:
        fh.write(entry)

    return f"✓ Saved to {notepad_path.resolve()}"


# ---------------------------------------------------------------------------
# Agent — agentic loop
# ---------------------------------------------------------------------------


def run_agent(transcript: str) -> None:
    """Send a transcript through Claude and handle tool calls until done."""
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        sys.exit("Error: ANTHROPIC_API_KEY environment variable is not set.")

    client = anthropic.Anthropic(api_key=api_key)

    messages: list[dict] = [
        {
            "role": "user",
            "content": (
                "Please analyze this sales confirmation call transcript, "
                "extract all key information, and save it to the notepad:\n\n"
                + transcript
            ),
        }
    ]

    print("\n  ● Analyzing transcript…\n")

    while True:
        # Stream the response; get_final_message() collects the full message.
        with client.messages.stream(
            model=MODEL,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    "cache_control": {"type": "ephemeral"},  # cache the prompt
                }
            ],
            tools=TOOLS,
            messages=messages,
        ) as stream:
            response = stream.get_final_message()

        # Print any text the model produced.
        for block in response.content:
            if block.type == "text" and block.text.strip():
                for line in block.text.strip().splitlines():
                    print(f"  {line}")
                print()

        # Done — no more tool calls needed.
        if response.stop_reason == "end_turn":
            break

        if response.stop_reason != "tool_use":
            break

        tool_uses = [b for b in response.content if b.type == "tool_use"]
        if not tool_uses:
            break

        # Append assistant turn with the tool_use blocks.
        messages.append({"role": "assistant", "content": response.content})

        # Execute each tool and collect results.
        tool_results: list[dict] = []
        for tu in tool_uses:
            if tu.name == "save_to_notepad":
                print("  ● Saving to notepad…")
                result_text = save_to_notepad(tu.input)
                print(f"  {result_text}\n")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": result_text,
                    }
                )
            else:
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": tu.id,
                        "content": f"Unknown tool: {tu.name}",
                        "is_error": True,
                    }
                )

        messages.append({"role": "user", "content": tool_results})


# ---------------------------------------------------------------------------
# Demo transcript
# ---------------------------------------------------------------------------

DEMO_TRANSCRIPT = """\
CONFIRMER: Hi, may I speak with John Smith please?
PROSPECT: Yes, this is John.
CONFIRMER: Hi John, this is Sarah calling from Premier Home Solutions. I'm \
calling to confirm your appointment scheduled for this coming Thursday, \
April 24th at 2:00 PM. Will you still be available?
PROSPECT: Yes, Thursday at 2 PM works great for me.
CONFIRMER: Wonderful! And just to confirm, the appointment will be at your \
home address at 123 Oak Street, Springfield, correct?
PROSPECT: That's right, 123 Oak Street.
CONFIRMER: Perfect. Our representative will be coming out to give you a quote \
on the solar panel installation you inquired about. Do you have any questions \
before then?
PROSPECT: Actually, I wanted to ask — can we make it a bit later, like 3 PM? \
I have a meeting that might run long.
CONFIRMER: Of course! Let me update that to 3:00 PM on Thursday April 24th. \
Does that work?
PROSPECT: Yes, 3 PM is perfect. Also, my wife will be there too, so you can \
discuss it with both of us.
CONFIRMER: Great, I've noted that. Is there anything else I should know before \
the visit?
PROSPECT: No, I think that's it. Oh wait — can you send me a confirmation \
email? My email is john.smith@email.com.
CONFIRMER: Absolutely, we'll send that right over to john.smith@email.com. \
And the best number to reach you is the one I called, which ends in 5678?
PROSPECT: Yes, that's my cell. 555-867-5678.
CONFIRMER: Perfect. So to confirm: Thursday April 24th at 3:00 PM at \
123 Oak Street for the solar panel installation consultation. Is there \
anything else?
PROSPECT: Nope, that covers it. Thank you!
CONFIRMER: Thank you John! We'll see you Thursday. Have a great day!
PROSPECT: You too, bye!\
"""


# ---------------------------------------------------------------------------
# CLI modes
# ---------------------------------------------------------------------------


def live_mode() -> None:
    """Enter call turns one by one; analyze when done."""
    print()
    print("  LIVE MODE")
    print("  Enter each call turn, then type 'done' when the call is over.")
    print("  Suggested format:  CONFIRMER: ...  or  PROSPECT: ...")
    print("  Type 'quit' to exit.")
    print()

    turns: list[str] = []
    while True:
        try:
            line = input("  > ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return

        if line.lower() in ("quit", "exit"):
            sys.exit(0)

        if line.lower() == "done":
            if not turns:
                print("  No turns recorded yet.")
                continue
            break

        if line:
            turns.append(line)

    run_agent("\n".join(turns))


def transcript_mode() -> None:
    """Paste a full transcript; analyze it when END is entered."""
    print()
    print("  TRANSCRIPT MODE")
    print("  Paste or type the full call transcript below.")
    print("  Enter a line containing only 'END' when finished.")
    print()

    lines: list[str] = []
    while True:
        try:
            line = input()
        except (EOFError, KeyboardInterrupt):
            break
        if line.strip().upper() == "END":
            break
        lines.append(line)

    transcript = "\n".join(lines).strip()
    if not transcript:
        print("  No transcript provided.")
        return

    run_agent(transcript)


def demo_mode() -> None:
    """Run on the built-in example transcript."""
    print()
    print("  DEMO MODE — Built-in example transcript:")
    print()
    for line in DEMO_TRANSCRIPT.splitlines():
        print(f"  {line}")
    print()
    run_agent(DEMO_TRANSCRIPT)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    print()
    print("  ╔══════════════════════════════════════════════╗")
    print("  ║      SALES CONFIRMATION CALL AGENT           ║")
    print("  ╚══════════════════════════════════════════════╝")
    print()
    print("  Select a mode:")
    print("    1  Live mode       — enter turns one by one")
    print("    2  Transcript mode — paste a full transcript")
    print("    3  Demo mode       — run the built-in example")
    print("    q  Quit")
    print()

    try:
        choice = input("  › ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        sys.exit(0)

    dispatch = {
        "1": live_mode,
        "live": live_mode,
        "2": transcript_mode,
        "transcript": transcript_mode,
        "3": demo_mode,
        "demo": demo_mode,
    }

    fn = dispatch.get(choice)
    if fn:
        fn()
    elif choice in ("q", "quit", "exit"):
        sys.exit(0)
    else:
        print("  Invalid choice.")
        sys.exit(1)


if __name__ == "__main__":
    main()
