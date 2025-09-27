"""Stream responses from Gemini using google-genai.

Requirements
------------
    pip install google-genai

Set the GEMINI_API_KEY environment variable before running.
"""

from __future__ import annotations

import argparse
import os
from typing import Iterable

from google import genai
from google.genai import types

MODEL_NAME = "gemini-2.5-flash"
PLACEHOLDER_TEXT = "INSERT_INPUT_HERE"


def stream_generation(client: genai.Client, prompt: str) -> Iterable[str]:
    """Yield streamed text chunks from Gemini."""
    contents = [
        types.Content(
            role="user",
            parts=[types.Part.from_text(text=prompt)],
        )
    ]
    config = types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(thinking_budget=-1)
    )

    for chunk in client.models.generate_content_stream(
        model=MODEL_NAME,
        contents=contents,
        config=config,
    ):
        if chunk.text:
            yield chunk.text


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Stream a Gemini response for the provided prompt.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Prompt text to send to Gemini (falls back to PLACEHOLDER_TEXT if omitted).",
    )
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment")

    client = genai.Client(api_key=api_key)

    prompt = args.prompt if args.prompt is not None else PLACEHOLDER_TEXT
    if prompt == PLACEHOLDER_TEXT:
        raise ValueError(
            "Prompt is still the placeholder text. Provide a prompt argument or edit PLACEHOLDER_TEXT.",
        )

    try:
        for piece in stream_generation(client, prompt):
            print(piece, end="")
    finally:
        client.close()


if __name__ == "__main__":
    main()
