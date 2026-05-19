import argparse
import sys
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LANGUAGE_DIR = ROOT / "languages"
PROTECTED_PATTERN = re.compile(
    r"`[^`]+`|https?://\S+|<[@#&]?\d+>|<a?:\w+:\d+>|\{[^{}]*\}|\{\}"
)


def load_json(path):
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    return data if isinstance(data, dict) else {}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Check translated language JSON catalogs.")
    parser.add_argument("--source", default="en")
    parser.add_argument("--target", default="es")
    parser.add_argument("--show", type=int, default=10)
    args = parser.parse_args()

    source = load_json(LANGUAGE_DIR / f"{args.source}.json")
    target = load_json(LANGUAGE_DIR / f"{args.target}.json")

    missing = sorted(set(source) - set(target))
    extra = sorted(set(target) - set(source))
    unchanged = [
        key for key, value in source.items()
        if target.get(key) == value and any(character.isalpha() for character in value)
    ]
    mismatches = []
    for key, value in source.items():
        source_tokens = PROTECTED_PATTERN.findall(value)
        target_tokens = PROTECTED_PATTERN.findall(target.get(key, ""))
        if source_tokens != target_tokens:
            mismatches.append((key, source_tokens, target_tokens, target.get(key, "")))

    print(f"{args.source} strings: {len(source)}")
    print(f"{args.target} strings: {len(target)}")
    print(f"missing keys: {len(missing)}")
    print(f"extra keys: {len(extra)}")
    print(f"unchanged alpha values: {len(unchanged)}")
    print(f"protected token mismatches: {len(mismatches)}")

    for key, source_tokens, target_tokens, value in mismatches[:args.show]:
        print(json.dumps({
            "key": key,
            "source_tokens": source_tokens,
            "target_tokens": target_tokens,
            "target_value": value,
        }, ensure_ascii=False))


if __name__ == "__main__":
    main()
