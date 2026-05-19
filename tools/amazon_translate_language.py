import argparse
import json
import os
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LANGUAGE_DIR = ROOT / "languages"


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


def boto3_client(region_name):
    try:
        import boto3
    except ImportError:
        return None
    return boto3.client("translate", region_name=region_name)


def translate_with_boto3(client, text, source_language, target_language):
    result = client.translate_text(
        Text=text,
        SourceLanguageCode=source_language,
        TargetLanguageCode=target_language,
    )
    return result["TranslatedText"]


def translate_with_aws_cli(text, source_language, target_language, region_name):
    command = [
        "aws",
        "translate",
        "translate-text",
        "--region",
        region_name,
        "--source-language-code",
        source_language,
        "--target-language-code",
        target_language,
        "--text",
        text,
    ]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Amazon Translate failed.").strip()
        raise RuntimeError(message)
    return json.loads(completed.stdout)["TranslatedText"]


def translate_text(client, text, source_language, target_language, region_name):
    if client is not None:
        return translate_with_boto3(client, text, source_language, target_language)
    return translate_with_aws_cli(text, source_language, target_language, region_name)


def main():
    parser = argparse.ArgumentParser(
        description="Translate a language JSON catalog with Amazon Translate."
    )
    parser.add_argument("--source", default="en", help="Source language code and JSON file name.")
    parser.add_argument("--target", default="es", help="Target language code and JSON file name.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Retranslate strings even when the target already has a non-English value.",
    )
    parser.add_argument(
        "--save-every",
        type=int,
        default=10,
        help="Save progress after this many translated strings.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.0,
        help="Seconds to sleep between Amazon Translate calls.",
    )
    args = parser.parse_args()

    region_name = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    source_path = LANGUAGE_DIR / f"{args.source}.json"
    target_path = LANGUAGE_DIR / f"{args.target}.json"

    source = load_json(source_path)
    if not source:
        raise SystemExit(f"No strings found in {source_path.relative_to(ROOT)}")

    target = load_json(target_path)
    client = boto3_client(region_name)
    translated_count = 0
    skipped_count = 0

    for index, (key, english_text) in enumerate(source.items(), start=1):
        existing = target.get(key)
        if existing and existing != english_text and not args.force:
            skipped_count += 1
            continue

        target[key] = translate_text(
            client,
            english_text,
            args.source,
            args.target,
            region_name,
        )
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
