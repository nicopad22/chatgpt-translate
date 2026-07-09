#!/usr/bin/env python3
"""
translate.py — CLI for translating Office documents.

Usage:
    python translate.py <output-language> <file-or-folder>

Examples:
    python translate.py spanish report.docx
    python translate.py english ./documents/

Output files are saved alongside the input:
    report.docx  →  report [SPANISH].docx
"""

import logging
import os
import sys
import time
from pathlib import Path

# ── Load .env for OPENAI_API_KEY ────────────────────────────────────────
# Try python-dotenv first; fall back to manual parsing so the script works
# even without the package installed.

_env_path = Path(__file__).resolve().parent / ".env"

try:
    from dotenv import load_dotenv
    load_dotenv(_env_path)
except ImportError:
    if _env_path.exists():
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())

import openai  # noqa: E402 (must come after env loading)
import ooxml_translate  # noqa: E402

# ── Logging ─────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────────

SUPPORTED_EXT = {".docx", ".pptx", ".xlsx"}

# Map common language inputs → display names for the output filename tag.
_LANG_NAMES = {
    "spanish": "SPANISH",   "es": "SPANISH",
    "english": "ENGLISH",   "en": "ENGLISH",
    "french":  "FRENCH",    "fr": "FRENCH",
    "german":  "GERMAN",    "de": "GERMAN",
    "italian": "ITALIAN",   "it": "ITALIAN",
    "portuguese": "PORTUGUESE", "pt": "PORTUGUESE",
    "chinese": "CHINESE",   "zh": "CHINESE",
    "japanese": "JAPANESE",  "ja": "JAPANESE",
    "korean":  "KOREAN",    "ko": "KOREAN",
    "russian": "RUSSIAN",   "ru": "RUSSIAN",
    "arabic":  "ARABIC",    "ar": "ARABIC",
    "dutch":   "DUTCH",     "nl": "DUTCH",
}

# ── Helpers ─────────────────────────────────────────────────────────────


def _output_path(input_path: Path, lang_tag: str) -> Path:
    """report.docx → report [SPANISH].docx"""
    return input_path.parent / f"{input_path.stem} [{lang_tag}]{input_path.suffix}"


# ── Main ────────────────────────────────────────────────────────────────


def main():
    # ── Argument parsing ───────────────────────────────────────────────
    if len(sys.argv) != 3:
        print("Usage: python translate.py <output-language> <file-or-folder>")
        print("Example: python translate.py spanish report.docx")
        sys.exit(1)

    language = sys.argv[1]
    target = sys.argv[2]
    lang_tag = _LANG_NAMES.get(language.lower(), language.upper())

    # ── API key ────────────────────────────────────────────────────────
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print()
        print("❌ ERROR: OPENAI_API_KEY not found.")
        print()
        print("Create a .env file in the project root with:")
        print("    OPENAI_API_KEY=sk-your-key-here")
        print()
        print("You can get your API key at: https://platform.openai.com/api-keys")
        print()
        sys.exit(1)

    client = openai.OpenAI(api_key=api_key)

    def llm_call(system_prompt: str, user_prompt: str) -> str:
        result = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
        )
        return result.choices[0].message.content

    # ── Collect files ──────────────────────────────────────────────────
    target_path = Path(target).resolve()

    if target_path.is_file():
        if target_path.suffix.lower() not in SUPPORTED_EXT:
            print(f"❌ Unsupported file type: {target_path.suffix}")
            sys.exit(1)
        files = [target_path]
    elif target_path.is_dir():
        files = sorted(
            f for f in target_path.iterdir()
            if f.is_file() and f.suffix.lower() in SUPPORTED_EXT
        )
    else:
        print(f"❌ Not found: {target}")
        sys.exit(1)

    if not files:
        print("No supported files found (.docx, .pptx, .xlsx)")
        sys.exit(0)

    print(f"\n📄 Found {len(files)} file(s) to translate to {lang_tag}:\n")
    for f in files:
        print(f"  • {f.name}")
    print()

    # ── Translate ──────────────────────────────────────────────────────
    translated_count = 0
    skipped_count = 0

    for i, filepath in enumerate(files, 1):
        out = _output_path(filepath, lang_tag)

        if out.exists():
            log.info("[%d/%d] Skipping (output exists): %s", i, len(files), filepath.name)
            skipped_count += 1
            continue

        log.info("[%d/%d] Translating: %s", i, len(files), filepath.name)

        words_done = [0]
        start = time.time()

        def on_progress(words, _wd=words_done):
            _wd[0] += words

        try:
            ooxml_translate.translate_file(
                str(filepath), str(out), language, llm_call, on_progress,
            )
            elapsed = time.time() - start
            log.info(
                "  ✅ Done — %d words in %.1fs → %s",
                words_done[0], elapsed, out.name,
            )
            translated_count += 1
        except Exception as exc:
            log.error("  ❌ Error: %s", exc)
            # Remove partial output
            if out.exists():
                out.unlink()

    # ── Summary ────────────────────────────────────────────────────────
    print(f"\n{'=' * 50}")
    print(f"Done!  Translated: {translated_count}  |  Skipped: {skipped_count}")
    print()


if __name__ == "__main__":
    main()
