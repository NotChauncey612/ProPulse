import argparse
import ast
import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
LANGUAGE_DIR = ROOT / "languages"
SOURCE_DIRS = [ROOT / "classes", ROOT]
SOURCE_FILES = ["main.py"]
IGNORED_STRINGS = {
    "",
    "\u200b",
}


def source_paths():
    for path in (ROOT / "classes").glob("*.py"):
        yield path
    for filename in SOURCE_FILES:
        path = ROOT / filename
        if path.exists():
            yield path


def is_user_facing(text):
    text = str(text)
    if text in IGNORED_STRINGS:
        return False
    if len(text.strip()) < 2:
        return False
    if "/" in text and " " not in text and text.startswith(("data/", "http://", "https://")):
        return False
    if text.isidentifier() and text.islower():
        return False
    return any(ch.isalpha() for ch in text)


def strings_from_node(node):
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            if is_user_facing(child.value):
                yield child.value
        elif isinstance(child, ast.JoinedStr):
            pieces = []
            for value in child.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    pieces.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    pieces.append("{}")
            text = "".join(pieces)
            if is_user_facing(text):
                yield text


def extract_strings():
    strings = set()
    for path in source_paths():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        strings.update(strings_from_node(tree))
    return dict(sorted((text, text) for text in strings))


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


def translate_with_amazon(text, target_language_code):
    import os

    try:
        import boto3
    except ImportError:
        boto3 = None

    region_name = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or "us-east-1"
    if boto3 is not None:
        client = boto3.client("translate", region_name=region_name)
        result = client.translate_text(
            Text=text,
            SourceLanguageCode="en",
            TargetLanguageCode=target_language_code,
        )
        return result["TranslatedText"]

    command = [
        "aws",
        "translate",
        "translate-text",
        "--region",
        region_name,
        "--source-language-code",
        "en",
        "--target-language-code",
        target_language_code,
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


def main():
    parser = argparse.ArgumentParser(description="Extract bot strings and update language JSON files.")
    parser.add_argument("--target", default="es", help="Target language code to update.")
    parser.add_argument("--amazon", action="store_true", help="Translate missing target strings with Amazon Translate.")
    args = parser.parse_args()

    english = extract_strings()
    en_path = LANGUAGE_DIR / "en.json"
    target_path = LANGUAGE_DIR / f"{args.target}.json"

    existing_en = load_json(en_path)
    existing_target = load_json(target_path)

    merged_en = {**english, **{key: existing_en.get(key, value) for key, value in english.items()}}
    target = {
        key: existing_target.get(key, value)
        for key, value in merged_en.items()
    }

    if args.amazon:
        for key, english_text in merged_en.items():
            if target.get(key) and target[key] != english_text:
                continue
            try:
                target[key] = translate_with_amazon(english_text, args.target)
            except RuntimeError as exc:
                save_json(en_path, merged_en)
                save_json(target_path, target)
                raise SystemExit(f"Amazon Translate stopped before completion: {exc}") from exc

    save_json(en_path, merged_en)
    save_json(target_path, target)
    print(f"Wrote {len(merged_en)} strings to {en_path.relative_to(ROOT)}")
    print(f"Wrote {len(target)} strings to {target_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
