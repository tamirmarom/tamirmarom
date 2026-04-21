# Hinge Account Creation Agent

An autonomous agent that creates a [Hinge](https://hinge.co) dating account via the Hinge web app, powered by the Claude API and Playwright.

## How it works

The agent runs an agentic loop: it calls Claude (with tool use) to decide what to do next, then executes browser actions (click, type, screenshot, etc.) via Playwright and feeds the results back to Claude.  Steps that require human interaction — SMS verification, CAPTCHA — pause and prompt the operator.

```
profile.json  →  hinge_agent.py  →  Claude API (tool use)  →  Playwright  →  app.hinge.co
```

## Setup

```bash
pip install -r requirements.txt
playwright install chromium
```

## Usage

1. Copy and edit the sample profile:
   ```bash
   cp profile.json my_profile.json
   # fill in phone_number and real details
   ```

2. Add your photos to a `photos/` directory (referenced in `profile.json`).

3. Run the agent:
   ```bash
   export ANTHROPIC_API_KEY=sk-...
   python hinge_agent.py --profile my_profile.json
   ```

   Add `--headless` to run without a visible browser window.

## Profile fields

| Field | Description |
|---|---|
| `first_name` | Your first name |
| `date_of_birth` | `YYYY-MM-DD` |
| `gender` / `sexuality` | Used during onboarding |
| `height_ft` / `height_in` | Imperial height |
| `prompts` | Array of `{ prompt, answer }` objects |
| `photos` | Paths to photo files |
| `phone_number` | Leave empty to be prompted at runtime |

## Files

| File | Purpose |
|---|---|
| `hinge_agent.py` | Main agent script |
| `profile.json` | Sample profile configuration |
| `requirements.txt` | Python dependencies |
| `session_state.json` | Browser session saved after a run (auto-generated) |

## Notes

- The agent saves the browser session to `session_state.json` after each run so you can resume later.
- SMS OTP verification always requires a human — the agent will pause and prompt you.
- Use responsibly and in accordance with Hinge’s Terms of Service.
