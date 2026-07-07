"""One-shot live Gemini spike.

Run manually once GEMINI_API_KEY is present in .env:

    .venv/bin/python scripts/spike_gemini.py

Verifies the two things the provider layer assumes: a live structured
JSON response arrives on ``response.parsed``, and plain text generation
works on the pinned model. While you are in AI Studio, note the real
RPM/RPD numbers shown on the dashboard.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import os

from dotenv import load_dotenv

from app.llm import DEFAULT_MODEL, Confidence, GeminiProvider


def main() -> int:
    load_dotenv()
    if not os.environ.get("GEMINI_API_KEY"):
        print("GEMINI_API_KEY is not set. Put it in .env (see .env.example).")
        print("Use a key from a billing-disabled Google Cloud project.")
        return 1

    provider = GeminiProvider()

    print(f"model: {DEFAULT_MODEL}")
    print("1) plain text call...")
    result = provider.generate("Reply with exactly: CloudSentinel spike OK")
    print(f"   -> {result.text.strip()!r} (source={result.source})")

    print("2) structured call (response_schema=Confidence)...")
    result = provider.generate(
        "A cloud service's daily cost jumped from a stable $120/day to $310 "
        "on a single day. How confident are you that this is a real anomaly "
        "worth investigating?",
        system_instruction="You are a cloud cost analyst. Answer briefly.",
        response_schema=Confidence,
    )
    assert isinstance(result.parsed, Confidence), "parsed payload missing"
    print(f"   -> score={result.parsed.score} rationale={result.parsed.rationale!r}")

    print("spike OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
