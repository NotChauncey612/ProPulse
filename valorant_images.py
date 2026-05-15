import argparse
import html
import json
import re
import unicodedata
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright

from images import dump_cards_json


CARDS_PATH = Path("data/cards.json")
SAVE_DIR = Path("player_images")
CARD_SET_NAME = "VCT '26"
IMAGE_SET_NAME = "Valorant '26"
GAME_NAME = "Valorant"
VALORANT_ESPORTS_BASE = "https://valorantesports.com/en-US/"
VLR_BASE = "https://www.vlr.gg"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/NotChauncey612/ProPulse/main"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
DEFAULT_SOURCE_URLS = [
    VALORANT_ESPORTS_BASE,
]
VLR_TEAM_SEARCH_ALIASES = {
    "JD GAMING": "JDG",
    "Leviatan Esports": "Leviatan",
    "TYLOO GAMING": "TYLOO",
}


def clean_filename(text):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)


def make_slug(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def normalize_match_text(text):
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def github_raw_url(filepath):
    path = Path(filepath).as_posix()
    encoded_path = quote(path, safe="/")
    return f"{GITHUB_RAW_BASE}/{encoded_path}"


def source_extension(url):
    path = unquote(urlparse(url).path)
    extension = Path(path).suffix.lower()

    if extension in IMAGE_EXTENSIONS:
        return extension

    return ".png"


def load_cards():
    with CARDS_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def iter_valorant_card_contexts(card_data):
    game_data = card_data.get("games", {}).get(GAME_NAME, {})

    for set_name, set_data in game_data.get("sets", {}).items():
        if set_name != CARD_SET_NAME:
            continue

        for league_name, league_data in set_data.get("leagues", {}).items():
            for team_name, team_data in league_data.get("teams", {}).items():
                for card in team_data.get("cards", []):
                    yield league_name, team_name, card


def iter_valorant_team_contexts(card_data):
    game_data = card_data.get("games", {}).get(GAME_NAME, {})

    for set_name, set_data in game_data.get("sets", {}).items():
        if set_name != CARD_SET_NAME:
            continue

        for league_name, league_data in set_data.get("leagues", {}).items():
            for team_name, team_data in league_data.get("teams", {}).items():
                yield league_name, team_name, team_data


def card_local_path(league_name, team_name, team_slug, player_ign, extension):
    return (
        SAVE_DIR
        / IMAGE_SET_NAME
        / league_name
        / clean_filename(team_name)
        / f"{team_slug}_{clean_filename(player_ign.lower())}{extension}"
    )


def download_image(url, filepath):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    Path(filepath).parent.mkdir(parents=True, exist_ok=True)

    with open(filepath, "wb") as f:
        f.write(response.content)


def image_is_likely_player_asset(src):
    src_lower = unquote(src).lower()
    extension = Path(urlparse(src_lower).path).suffix.lower()

    if extension and extension not in IMAGE_EXTENSIONS:
        return False

    if any(token in src_lower for token in ["/teams/", "/leagues/", "logo", "spark"]):
        return False

    return (
        "/players/" in src_lower
        or "%2fplayers%2f" in src_lower
        or "player" in src_lower
        or "headshot" in src_lower
        or "portrait" in src_lower
        or "valorant" in src_lower
        or "val_esports" in src_lower
    )


def extract_html_images(page_url):
    response = requests.get(page_url, timeout=30)
    response.raise_for_status()
    html = response.text

    results = []
    patterns = [
        r'<img[^>]+(?:src|srcSet|imageSrcSet)=["\']([^"\']+)["\']',
        r'url\(["\']?([^"\')]+)["\']?\)',
        r'https?://[^"\')\s<>]+?\.(?:png|jpg|jpeg|webp)(?:\?[^"\')\s<>]*)?',
    ]

    for pattern in patterns:
        for match in re.finditer(pattern, html, flags=re.IGNORECASE):
            raw = match.group(1) if match.lastindex else match.group(0)

            for src in split_srcset(raw):
                results.append({
                    "src": urljoin(page_url, src),
                    "text": surrounding_text(html, match.start(), match.end()),
                    "source": page_url,
                    "type": "html",
                })

    return results


def split_srcset(value):
    items = []

    for part in value.split(","):
        src = part.strip().split(" ")[0].strip()

        if src:
            items.append(src)

    return items


def surrounding_text(text, start, end, radius=240):
    return text[max(0, start - radius):min(len(text), end + radius)]


def scrape_rendered_images(page_url, headless=True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()
        page.goto(page_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        images = page.evaluate(
            """
            () => {
                const results = [];

                document.querySelectorAll("img").forEach(img => {
                    if (!img.src) return;
                    results.push({
                        type: "img",
                        src: img.currentSrc || img.src,
                        text: [
                            img.alt || "",
                            img.title || "",
                            img.getAttribute("aria-label") || "",
                            img.closest("a")?.innerText || "",
                            img.parentElement?.innerText || ""
                        ].join(" ")
                    });
                });

                document.querySelectorAll("*").forEach(el => {
                    const style = window.getComputedStyle(el);
                    const bg = style.backgroundImage;
                    if (!bg || bg === "none") return;
                    const match = bg.match(/url\\(["']?(.*?)["']?\\)/);
                    if (!match || !match[1]) return;
                    results.push({
                        type: "background",
                        src: match[1],
                        text: [
                            el.innerText || "",
                            el.getAttribute("aria-label") || "",
                            el.getAttribute("title") || ""
                        ].join(" ")
                    });
                });

                return results;
            }
            """
        )

        browser.close()

    for image in images:
        image["src"] = urljoin(page_url, image["src"])
        image["source"] = page_url

    return images


def extract_image_haystack(image):
    src = unquote(image.get("src", ""))
    src_path = urlparse(src).path
    filename = Path(src_path).stem

    return normalize_match_text(
        " ".join([
            image.get("text", ""),
            src,
            src_path,
            filename,
        ])
    )


def find_player_match(image, missing_cards):
    haystack = extract_image_haystack(image)
    raw_text = unquote(f"{image.get('text', '')} {image.get('src', '')}").lower()

    for league_name, team_name, card in sorted(
        missing_cards,
        key=lambda item: len(item[2].get("ign", "")),
        reverse=True,
    ):
        ign = card.get("ign", "")
        normalized_ign = normalize_match_text(ign)

        if not normalized_ign:
            continue

        if len(normalized_ign) <= 3:
            pattern = rf"(^|[^a-z0-9]){re.escape(normalized_ign)}([^a-z0-9]|$)"

            if re.search(pattern, raw_text):
                return league_name, team_name, card

            continue

        if normalized_ign in haystack:
            return league_name, team_name, card

    return None


def link_existing_local_images(card_data):
    linked = 0

    for league_name, team_name, card in iter_valorant_card_contexts(card_data):
        if card.get("image"):
            continue

        team_slug = make_slug(team_name)
        player_slug = clean_filename(card["ign"].lower())
        team_folder = SAVE_DIR / IMAGE_SET_NAME / league_name / clean_filename(team_name)

        for extension in IMAGE_EXTENSIONS:
            filepath = team_folder / f"{team_slug}_{player_slug}{extension}"

            if filepath.exists():
                card["image"] = github_raw_url(filepath)
                linked += 1
                break

    return linked


def print_missing_images(card_data):
    total = 0
    missing_total = 0

    print("\nMissing Valorant image links:")

    for league_name, team_name, team_data in iter_valorant_team_contexts(card_data):
        missing = [
            card for card in team_data.get("cards", [])
            if not card.get("image")
        ]

        total += len(team_data.get("cards", []))
        missing_total += len(missing)

        if missing:
            print(f"\n{league_name} - {team_name}")

            for card in missing:
                print(f"  - {card['ign']}")

    if not missing_total:
        print("None. Every Valorant card has an image.")

    print(f"\nValorant image coverage: {total - missing_total}/{total}")


def selected_missing_cards(card_data, requested_teams):
    cards = []

    for league_name, team_name, card in iter_valorant_card_contexts(card_data):
        if card.get("image"):
            continue

        if requested_teams and make_slug(team_name) not in requested_teams:
            continue

        cards.append((league_name, team_name, card))

    return cards


def scrape_sources(card_data, source_urls, requested_teams, headless=True, dry_run=False):
    saved = 0
    seen_sources = set()

    for source_url in source_urls:
        source_url = source_url.strip()

        if not source_url or source_url in seen_sources:
            continue

        seen_sources.add(source_url)
        print(f"\nScraping {source_url}")

        try:
            images = extract_html_images(source_url)
        except Exception as e:
            print(f"  Could not read page HTML: {e}")
            images = []

        try:
            images.extend(scrape_rendered_images(source_url, headless=headless))
        except Exception as e:
            print(f"  Could not scrape rendered page: {e}")

        missing_cards = selected_missing_cards(card_data, requested_teams)

        if not missing_cards:
            break

        matched_cards = set()

        for index, image in enumerate(images):
            src = image.get("src", "")

            if not src or src in matched_cards:
                continue

            if not image_is_likely_player_asset(src):
                continue

            match = find_player_match(image, missing_cards)

            if not match:
                continue

            league_name, team_name, card = match
            team_slug = make_slug(team_name)
            extension = source_extension(src)
            filepath = card_local_path(
                league_name,
                team_name,
                team_slug,
                card["ign"],
                extension,
            )

            if dry_run:
                print(f"  Would save {card['ign']} from {src}")
                matched_cards.add(src)
                continue

            try:
                download_image(src, filepath)
            except Exception as e:
                print(f"  Skipped image {index} ({src}): {e}")
                continue

            card["image"] = github_raw_url(filepath)
            saved += 1
            matched_cards.add(src)
            print(f"  Saved {filepath}")

    return saved


def vlr_get(url):
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    response.raise_for_status()
    return response


def normalize_team_name(text):
    replacements = {
        "kru": "kr",
        "kru esports": "kr esports",
        "krue sports": "kr esports",
        "leviatan esports": "leviatan esports",
        "leviatn esports": "leviatan esports",
        "edward gaming": "edward gaming",
        "edwardgaming": "edward gaming",
        "nongshim redforce": "nongshim redforce",
        "nongshim red force": "nongshim redforce",
    }
    normalized = normalize_match_text(text)

    for source, target in replacements.items():
        normalized = normalized.replace(normalize_match_text(source), normalize_match_text(target))

    return normalized


def vlr_search_team(team_name):
    search_name = VLR_TEAM_SEARCH_ALIASES.get(team_name, team_name)
    search_url = f"{VLR_BASE}/search/?q={quote(search_name)}"
    response = vlr_get(search_url)
    page = response.text
    candidates = []

    pattern = re.compile(
        r'<a href="(?P<href>/search/r/team/\d+/idx)"[^>]*class="[^"]*search-item[^"]*"[^>]*>'
        r'(?P<body>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )

    for match in pattern.finditer(page):
        body = match.group("body")
        title_match = re.search(
            r'<div class="search-item-title">\s*(?P<title>.*?)\s*</div>',
            body,
            re.IGNORECASE | re.DOTALL,
        )

        if not title_match:
            continue

        title = html.unescape(re.sub(r"<.*?>", "", title_match.group("title"))).strip()
        title = re.sub(r"\s+", " ", title)
        candidates.append((title, urljoin(VLR_BASE, match.group("href"))))

    wanted = normalize_team_name(search_name)

    for title, url in candidates:
        if normalize_team_name(title) == wanted:
            return vlr_get(url).url

    for title, url in candidates:
        title_key = normalize_team_name(title)

        if wanted in title_key or title_key in wanted:
            return vlr_get(url).url

    return None


def vlr_roster_images(team_url):
    response = vlr_get(team_url)
    page = response.text
    players = {}

    roster_match = re.search(
        r'<h2[^>]*>\s*Current\s*Roster\s*</h2>(?P<body>.*?)(?:<h2|\Z)',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    roster_html = roster_match.group("body") if roster_match else page

    item_pattern = re.compile(
        r'<div class="team-roster-item">(?P<body>.*?)</div>\s*</div>\s*</a>\s*</div>',
        re.IGNORECASE | re.DOTALL,
    )

    for match in item_pattern.finditer(roster_html):
        body = match.group("body")
        img_match = re.search(r'<img[^>]+src=["\'](?P<src>[^"\']+)["\']', body, re.IGNORECASE)
        alias_match = re.search(
            r'<div class="team-roster-item-name-alias">\s*(?P<alias>.*?)\s*</div>',
            body,
            re.IGNORECASE | re.DOTALL,
        )

        if not img_match or not alias_match:
            continue

        alias = re.sub(r"<.*?>", " ", alias_match.group("alias"))
        alias = html.unescape(alias)
        alias = re.sub(r"\s+", " ", alias).strip()

        if not alias:
            continue

        src = html.unescape(img_match.group("src"))
        players[normalize_match_text(alias)] = urljoin(team_url, src)

    return players


def vlr_player_search_image(player_ign):
    search_url = f"{VLR_BASE}/search/?q={quote(player_ign)}"
    response = vlr_get(search_url)
    page = response.text

    pattern = re.compile(
        r'<a href="(?P<href>/search/r/player/\d+/idx)"[^>]*class="[^"]*search-item[^"]*"[^>]*>'
        r'(?P<body>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    )
    wanted = normalize_match_text(player_ign)

    for match in pattern.finditer(page):
        body = match.group("body")
        title_match = re.search(
            r'<div class="search-item-title">\s*(?P<title>.*?)\s*</div>',
            body,
            re.IGNORECASE | re.DOTALL,
        )
        img_match = re.search(r'<img[^>]+src=["\'](?P<src>[^"\']+)["\']', body, re.IGNORECASE)

        if not title_match or not img_match:
            continue

        title = html.unescape(re.sub(r"<.*?>", " ", title_match.group("title"))).strip()

        if normalize_match_text(title) != wanted:
            continue

        src = html.unescape(img_match.group("src"))

        if "/img/base/ph/" in src:
            return None

        return urljoin(VLR_BASE, src)

    return None


def scrape_vlr(card_data, requested_teams, dry_run=False):
    saved = 0

    for league_name, team_name, team_data in iter_valorant_team_contexts(card_data):
        if requested_teams and make_slug(team_name) not in requested_teams:
            continue

        missing = [
            card for card in team_data.get("cards", [])
            if not card.get("image")
        ]

        if not missing:
            continue

        team_url = vlr_search_team(team_name)

        if not team_url:
            print(f"\nVLR team not found: {team_name}")
            continue

        print(f"\nScraping {team_name} from {team_url}")

        try:
            roster_images = vlr_roster_images(team_url)
        except Exception as e:
            print(f"  Could not read VLR roster: {e}")
            continue

        for card in missing:
            image_url = roster_images.get(normalize_match_text(card["ign"]))

            if not image_url:
                image_url = vlr_player_search_image(card["ign"])

            if not image_url:
                print(f"  No image found for {card['ign']}")
                continue

            team_slug = make_slug(team_name)
            extension = source_extension(image_url)
            filepath = card_local_path(
                league_name,
                team_name,
                team_slug,
                card["ign"],
                extension,
            )

            if dry_run:
                print(f"  Would save {card['ign']} from {image_url}")
                continue

            try:
                download_image(image_url, filepath)
            except Exception as e:
                print(f"  Could not download {card['ign']}: {e}")
                continue

            card["image"] = github_raw_url(filepath)
            saved += 1
            print(f"  Saved {filepath}")

    return saved


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download Valorant player images and update data/cards.json."
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print missing Valorant card images without downloading or rewriting cards.json.",
    )
    parser.add_argument(
        "--show-browser",
        action="store_true",
        help="Run Playwright with a visible browser window.",
    )
    parser.add_argument(
        "--team",
        action="append",
        default=[],
        help="Only scrape a team slug or team name. Can be used more than once.",
    )
    parser.add_argument(
        "--source-url",
        action="append",
        default=[],
        help=(
            "Public Valorant Esports page to scan for player image assets. "
            "Can be used more than once."
        ),
    )
    parser.add_argument(
        "--source",
        choices=["vlr", "valorantesports", "both"],
        default="vlr",
        help="Image source to use. Default: vlr.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matched image assets without downloading or updating cards.json.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    card_data = load_cards()

    if args.report_only:
        print_missing_images(card_data)
        return

    SAVE_DIR.mkdir(exist_ok=True)

    requested_teams = {make_slug(team) for team in args.team}

    linked = link_existing_local_images(card_data)

    if linked:
        print(f"Linked {linked} existing local image(s).")

    saved = 0

    if args.source in {"vlr", "both"}:
        saved += scrape_vlr(card_data, requested_teams, dry_run=args.dry_run)

    if args.source in {"valorantesports", "both"}:
        source_urls = args.source_url or DEFAULT_SOURCE_URLS
        saved += scrape_sources(
            card_data,
            source_urls,
            requested_teams,
            headless=not args.show_browser,
            dry_run=args.dry_run,
        )

    if not args.dry_run:
        dump_cards_json(card_data)

    print_missing_images(card_data)
    print(f"\nSaved {saved} new Valorant image(s).")

    if not saved:
        print(
            "No matching player assets were found on the scanned Valorant "
            "Esports page(s). Try adding --source-url for a roster, article, "
            "or event page that visibly contains player portraits."
        )


if __name__ == "__main__":
    main()
