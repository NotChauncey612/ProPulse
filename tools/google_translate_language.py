import argparse
import json
import re
import time
from pathlib import Path

from deep_translator import GoogleTranslator


ROOT = Path(__file__).resolve().parent.parent
LANGUAGE_DIR = ROOT / "languages"
PROTECTED_PATTERN = re.compile(
    r"`[^`]+`|https?://\S+|<[@#&]?\d+>|<a?:\w+:\d+>|\{[^{}]*\}|\{\}"
)


def load_json(path):
    try:
        with open(path, "r", encoding="utf-8") as file:
            data = json.load(file)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def save_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=4)
        file.write("\n")


def translate_plain_segment(translator, text, cache):
    leading_match = re.match(r"^\s*", text)
    trailing_match = re.search(r"\s*$", text)
    leading = leading_match.group(0) if leading_match else ""
    trailing = trailing_match.group(0) if trailing_match else ""
    core = text[len(leading):len(text) - len(trailing) if trailing else len(text)]

    if not core or not any(character.isalpha() for character in core):
        return text

    if core in cache:
        translated = cache[core]
    else:
        translated = translator.translate(core)
        cache[core] = translated

    if translated is None:
        return text
    return leading + translated.strip() + trailing


def translate_value(translator, text, cache):
    if not text or not any(character.isalpha() for character in text):
        return text

    pieces = []
    last_end = 0
    for match in PROTECTED_PATTERN.finditer(text):
        pieces.append(translate_plain_segment(translator, text[last_end:match.start()], cache))
        pieces.append(match.group(0))
        last_end = match.end()
    pieces.append(translate_plain_segment(translator, text[last_end:], cache))
    return "".join(pieces)


def main():
    parser = argparse.ArgumentParser(
        description="Translate a language JSON catalog with Google Translate via deep-translator."
    )
    parser.add_argument("--source", default="en", help="Source language code and JSON file name.")
    parser.add_argument("--target", default="es", help="Target language code and JSON file name.")
    parser.add_argument("--force", action="store_true", help="Retranslate existing target values.")
    parser.add_argument("--save-every", type=int, default=10, help="Save progress after this many translations.")
    parser.add_argument("--sleep", type=float, default=0.15, help="Seconds to sleep between translation calls.")
    args = parser.parse_args()

    source_path = LANGUAGE_DIR / f"{args.source}.json"
    target_path = LANGUAGE_DIR / f"{args.target}.json"
    source = load_json(source_path)
    target = load_json(target_path)

    if not source:
        raise SystemExit(f"No strings found in {source_path.relative_to(ROOT)}")

    translator = GoogleTranslator(source=args.source, target=args.target)
    cache = {}
    translated_count = 0
    skipped_count = 0

    for index, (key, value) in enumerate(source.items(), start=1):
        existing = target.get(key)
        if existing and existing != value and not args.force:
            skipped_count += 1
            continue

        target[key] = translate_value(translator, value, cache)
        translated_count += 1

        if translated_count % args.save_every == 0:
            save_json(target_path, target)
            print(f"Saved {translated_count} translated string(s) after {index}/{len(source)} checked.")

        if args.sleep > 0:
            time.sleep(args.sleep)

    save_json(target_path, target)
    print(
        f"Done. Translated {translated_count} string(s), skipped {skipped_count} existing string(s), "
        f"wrote {target_path.relative_to(ROOT)}."
    )


if __name__ == "__main__":
    main()
