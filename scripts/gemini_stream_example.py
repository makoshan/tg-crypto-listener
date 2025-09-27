"""Stream responses from Gemini using google-genai.

Requirements
------------
    pip install google-genai

Set the GEMINI_API_KEY environment variable (or populate it in .env) before running.
Optional helpers:
    - Provide inline prompt text (`python scripts/gemini_stream_example.py "Hello"`).
    - Use `--file path/to/prompt.txt` to load from a file.
    - Define GEMINI_TEST_PROMPT in your environment or .env to reuse a default prompt.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Iterable

from google import genai
from google.genai import types

# Ensure project modules are importable when running from repo root.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:  # pragma: no cover - script execution guard
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import Config  # noqa: E402  pylint: disable=wrong-import-position

DEFAULT_MODEL = Config.AI_MODEL_NAME or "gemini-2.5-flash"
DEFAULT_PROMPT = (
    "Summarize the key crypto market implications of Bitcoin gaining 5% in the last 24 hours."
)
ENV_PROMPT_VAR = "GEMINI_TEST_PROMPT"


def stream_generation(client: genai.Client, prompt: str, model: str) -> Iterable[str]:
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
        model=model,
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
        help="Inline prompt text to send to Gemini.",
    )
    parser.add_argument(
        "-f",
        "--file",
        type=Path,
        help="Path to a file whose contents will be used as the prompt.",
    )
    parser.add_argument(
        "-m",
        "--model",
        default=None,
        help="Override the model name (defaults to AI_MODEL_NAME from config).",
    )
    args = parser.parse_args()

    api_key = os.getenv("GEMINI_API_KEY") or Config.GEMINI_API_KEY
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY not set in environment or config")

    model = args.model or DEFAULT_MODEL

    prompt_source = "inline argument"
    prompt: str | None = None

    if args.file:
        file_path = args.file
        if not file_path.exists():
            raise FileNotFoundError(f"Prompt file not found: {file_path}")
        prompt_source = f"file {file_path}"
        prompt = file_path.read_text(encoding="utf-8").strip()
    elif args.prompt is not None:
        prompt = args.prompt.strip()
    else:
        env_prompt = os.getenv(ENV_PROMPT_VAR)
        if env_prompt:
            prompt_source = f"environment variable {ENV_PROMPT_VAR}"
            prompt = env_prompt.strip()
        else:
            prompt_source = "built-in sample prompt"
            prompt = DEFAULT_PROMPT

    if not prompt:
        raise ValueError("Prompt is empty after loading inputs. Provide prompt text or use --file.")

    print(f"🔧 Using model: {model}")
    print(f"📝 Prompt source: {prompt_source}\n")

    client = genai.Client(api_key=api_key)

    try:
        for piece in stream_generation(client, prompt, model):
            print(piece, end="")
        print()
    finally:
        client.close()


if __name__ == "__main__":
    main()
