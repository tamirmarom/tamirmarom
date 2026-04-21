#!/usr/bin/env python3
"""
Hinge account creation agent.

Uses Claude API (tool use) + Playwright to walk through the Hinge web sign-up
flow at https://app.hinge.co.  Human-in-the-loop pauses are injected for
steps that require physical interaction (e.g. SMS OTP entry).

Usage:
    pip install -r requirements.txt
    playwright install chromium
    cp profile.json my_profile.json   # edit with your real details
    ANTHROPIC_API_KEY=sk-... python hinge_agent.py --profile my_profile.json
"""

import argparse
import asyncio
import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import anthropic
from dotenv import load_dotenv
from playwright.async_api import Page, async_playwright

load_dotenv()

HINGE_URL = "https://app.hinge.co"
MODEL = "claude-sonnet-4-6"
MAX_AGENT_STEPS = 60

# ---------------------------------------------------------------------------
# Tool definitions exposed to the Claude agent
# ---------------------------------------------------------------------------

TOOLS: list[dict] = [
    {
        "name": "screenshot",
        "description": (
            "Capture the current state of the browser as a PNG image so you can "
            "see what is on screen and decide what to do next."
        ),
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "navigate",
        "description": "Navigate the browser to a URL.",
        "input_schema": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "Absolute URL to navigate to."}
            },
            "required": ["url"],
        },
    },
    {
        "name": "click",
        "description": (
            "Click an element on the page.  Provide either a CSS/text selector "
            "OR pixel coordinates (x, y), not both."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector or visible text of the element to click.",
                },
                "x": {"type": "number", "description": "X coordinate (pixels)."},
                "y": {"type": "number", "description": "Y coordinate (pixels)."},
            },
        },
    },
    {
        "name": "type_text",
        "description": "Type text into the currently focused element or into a selector.",
        "input_schema": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "Text to type."},
                "selector": {
                    "type": "string",
                    "description": "Optional CSS selector to click/focus before typing.",
                },
                "clear_first": {
                    "type": "boolean",
                    "description": "Clear the field before typing (default true).",
                },
            },
            "required": ["text"],
        },
    },
    {
        "name": "select_option",
        "description": "Select an option in a <select> dropdown.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS selector for the <select>."},
                "value": {
                    "type": "string",
                    "description": "The option value or visible label to select.",
                },
            },
            "required": ["selector", "value"],
        },
    },
    {
        "name": "scroll",
        "description": "Scroll the page or an element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "enum": ["up", "down"],
                    "description": "Scroll direction.",
                },
                "amount": {
                    "type": "number",
                    "description": "Pixels to scroll (default 300).",
                },
            },
            "required": ["direction"],
        },
    },
    {
        "name": "wait",
        "description": "Wait for a fixed number of milliseconds (max 5000).",
        "input_schema": {
            "type": "object",
            "properties": {
                "ms": {"type": "number", "description": "Milliseconds to wait."}
            },
            "required": ["ms"],
        },
    },
    {
        "name": "get_page_text",
        "description": "Return the visible text content of the page (useful for reading form labels, error messages, etc.).",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
    {
        "name": "upload_file",
        "description": "Upload a file (e.g. a photo) to a file-input element.",
        "input_schema": {
            "type": "object",
            "properties": {
                "selector": {
                    "type": "string",
                    "description": "CSS selector for the file <input>.",
                },
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file on disk.",
                },
            },
            "required": ["selector", "file_path"],
        },
    },
    {
        "name": "ask_human",
        "description": (
            "Pause the agent and ask the human operator to perform a manual step "
            "(e.g. enter an SMS OTP, solve a CAPTCHA, upload a photo manually). "
            "The agent resumes once the human presses Enter."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Instructions to display to the human.",
                }
            },
            "required": ["message"],
        },
    },
    {
        "name": "finish",
        "description": (
            "Signal that the account creation flow is complete (or has reached a "
            "terminal state) and stop the agent loop."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "success": {
                    "type": "boolean",
                    "description": "True if the account was created successfully.",
                },
                "message": {
                    "type": "string",
                    "description": "Final status message.",
                },
            },
            "required": ["success", "message"],
        },
    },
]


# ---------------------------------------------------------------------------
# Browser tool implementations
# ---------------------------------------------------------------------------

async def tool_screenshot(page: Page) -> dict:
    png = await page.screenshot(full_page=False)
    b64 = base64.standard_b64encode(png).decode()
    return {"type": "image", "data": b64, "media_type": "image/png"}


async def tool_navigate(page: Page, url: str) -> str:
    await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    return f"Navigated to {url}"


async def tool_click(page: Page, selector: str | None, x: float | None, y: float | None) -> str:
    if selector:
        try:
            await page.click(selector, timeout=10_000)
            return f"Clicked selector: {selector}"
        except Exception:
            # Fall back to get_by_text
            await page.get_by_text(selector, exact=False).first.click(timeout=10_000)
            return f"Clicked text: {selector}"
    elif x is not None and y is not None:
        await page.mouse.click(x, y)
        return f"Clicked coordinates ({x}, {y})"
    return "No selector or coordinates provided"


async def tool_type_text(
    page: Page, text: str, selector: str | None, clear_first: bool
) -> str:
    if selector:
        await page.click(selector, timeout=10_000)
    if clear_first:
        await page.keyboard.press("Control+a")
        await page.keyboard.press("Backspace")
    await page.keyboard.type(text, delay=40)
    return f"Typed: {text!r}"


async def tool_select_option(page: Page, selector: str, value: str) -> str:
    try:
        await page.select_option(selector, label=value, timeout=10_000)
    except Exception:
        await page.select_option(selector, value=value, timeout=10_000)
    return f"Selected option {value!r} in {selector}"


async def tool_scroll(page: Page, direction: str, amount: float) -> str:
    delta = amount if direction == "down" else -amount
    await page.mouse.wheel(0, delta)
    return f"Scrolled {direction} by {amount}px"


async def tool_get_page_text(page: Page) -> str:
    text = await page.evaluate("() => document.body.innerText")
    return text[:4000]  # trim to avoid token explosion


async def tool_upload_file(page: Page, selector: str, file_path: str) -> str:
    resolved = str(Path(file_path).resolve())
    await page.set_input_files(selector, resolved)
    return f"Uploaded {resolved} to {selector}"


def tool_ask_human(message: str) -> str:
    print(f"\n{'='*60}")
    print("ACTION REQUIRED BY HUMAN OPERATOR:")
    print(message)
    print("Press Enter when done...")
    print("=" * 60)
    input()
    return "Human confirmed step completed."


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

async def dispatch_tool(page: Page, name: str, inputs: dict) -> Any:
    if name == "screenshot":
        return await tool_screenshot(page)
    if name == "navigate":
        return await tool_navigate(page, inputs["url"])
    if name == "click":
        return await tool_click(
            page,
            inputs.get("selector"),
            inputs.get("x"),
            inputs.get("y"),
        )
    if name == "type_text":
        return await tool_type_text(
            page,
            inputs["text"],
            inputs.get("selector"),
            inputs.get("clear_first", True),
        )
    if name == "select_option":
        return await tool_select_option(page, inputs["selector"], inputs["value"])
    if name == "scroll":
        return await tool_scroll(page, inputs["direction"], inputs.get("amount", 300))
    if name == "wait":
        ms = min(int(inputs.get("ms", 1000)), 5000)
        await asyncio.sleep(ms / 1000)
        return f"Waited {ms}ms"
    if name == "get_page_text":
        return await tool_get_page_text(page)
    if name == "upload_file":
        return await tool_upload_file(page, inputs["selector"], inputs["file_path"])
    if name == "ask_human":
        return tool_ask_human(inputs["message"])
    if name == "finish":
        return None  # handled in the loop
    return f"Unknown tool: {name}"


# ---------------------------------------------------------------------------
# Build tool result content block
# ---------------------------------------------------------------------------

def make_tool_result(tool_use_id: str, result: Any) -> dict:
    if isinstance(result, dict) and result.get("type") == "image":
        return {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": [
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": result["media_type"],
                        "data": result["data"],
                    },
                }
            ],
        }
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": str(result),
    }


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

def build_system_prompt(profile: dict) -> str:
    return f"""You are an autonomous agent that creates a Hinge dating account via the Hinge web app.

## Your goal
Complete the full account sign-up flow at https://app.hinge.co using the profile data below.

## Profile data
{json.dumps(profile, indent=2)}

## Instructions
1. Start by navigating to {HINGE_URL}.
2. Take a screenshot first to see the current state of the page.
3. Follow the sign-up / onboarding flow step by step.
4. For phone number entry: if profile.phone_number is empty, call ask_human to get the number.
5. For SMS OTP verification: always call ask_human — you cannot receive SMS.
6. For photo uploads: if the profile lists photo paths, attempt to upload them via upload_file.
   If the file does not exist, call ask_human to ask the operator to upload photos manually.
7. Fill in every profile field (name, DOB, gender, prompts, etc.) from the profile data.
8. After completing onboarding, take a final screenshot and call finish(success=true).
9. If you hit an unrecoverable error, call finish(success=false, message="...").

## Guidelines
- Always take a screenshot after navigation or a major action to confirm the result.
- If a step is unclear, take a screenshot and read the page text before acting.
- Prefer clicking visible buttons/links over injecting JavaScript.
- Be patient: after clicks, wait briefly (≈1s) for animations/page loads.
- Never attempt to bypass security checks or terms of service.
"""


# ---------------------------------------------------------------------------
# Agent loop
# ---------------------------------------------------------------------------

async def run_agent(page: Page, profile: dict) -> None:
    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

    messages: list[dict] = [
        {"role": "user", "content": "Please create my Hinge account using my profile data."}
    ]

    system = build_system_prompt(profile)

    for step in range(MAX_AGENT_STEPS):
        print(f"\n[step {step + 1}/{MAX_AGENT_STEPS}] Calling Claude...")

        response = client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=system,
            tools=TOOLS,
            messages=messages,
        )

        # Append assistant turn
        messages.append({"role": "assistant", "content": response.content})

        # Check stop reason
        if response.stop_reason == "end_turn":
            print("[agent] Model finished without calling finish().")
            break

        if response.stop_reason != "tool_use":
            print(f"[agent] Unexpected stop_reason: {response.stop_reason}")
            break

        # Process tool calls
        tool_results = []
        done = False

        for block in response.content:
            if block.type != "tool_use":
                continue

            tool_name = block.name
            tool_inputs = block.input
            print(f"  -> tool: {tool_name}  inputs: {json.dumps(tool_inputs)[:120]}")

            if tool_name == "finish":
                success = tool_inputs.get("success", False)
                message = tool_inputs.get("message", "")
                print(f"\n[agent] FINISHED — success={success}: {message}")
                done = True
                tool_results.append(make_tool_result(block.id, "Acknowledged."))
                break

            try:
                result = await dispatch_tool(page, tool_name, tool_inputs)
            except Exception as exc:
                result = f"ERROR: {exc}"
                print(f"  !! tool error: {exc}")

            tool_results.append(make_tool_result(block.id, result))

        # Feed tool results back
        if tool_results:
            messages.append({"role": "user", "content": tool_results})

        if done:
            break
    else:
        print("[agent] Reached max steps limit.")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def main(profile_path: str, headless: bool) -> None:
    profile_file = Path(profile_path)
    if not profile_file.exists():
        sys.exit(f"Profile file not found: {profile_path}")

    with open(profile_file) as f:
        profile = json.load(f)

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY environment variable is not set.")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=headless, slow_mo=100)
        context = await browser.new_context(
            viewport={"width": 1280, "height": 900},
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        )
        page = await context.new_page()

        try:
            await run_agent(page, profile)
        finally:
            await context.storage_state(path="session_state.json")
            print("\n[agent] Browser session saved to session_state.json")
            await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hinge account creation agent")
    parser.add_argument(
        "--profile",
        default="profile.json",
        help="Path to profile JSON (default: profile.json)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode (default: visible)",
    )
    args = parser.parse_args()

    asyncio.run(main(args.profile, args.headless))
