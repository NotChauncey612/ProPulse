import json
import html
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

import requests
from PIL import Image
from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from images import get_api_team, make_slug, source_extension
from valorant_images import vlr_get, vlr_search_team


CARDS_PATH = Path("data/cards.json")
ASSET_ROOT = Path("assets/cards")
TEAM_LOGO_DIR = ASSET_ROOT / "team_logos"
ROLE_ICON_DIR = ASSET_ROOT / "role_icons"
ROLE_ICON_SOURCES = {
    "TOP": "https://raw.communitydragon.org/9.9/plugins/rcp-fe-lol-leagues/global/default/svg/position-top.svg",
    "JNG": "https://raw.communitydragon.org/9.9/plugins/rcp-fe-lol-leagues/global/default/svg/position-jungle.svg",
    "MID": "https://raw.communitydragon.org/9.9/plugins/rcp-fe-lol-leagues/global/default/svg/position-middle.svg",
    "BOT": "https://raw.communitydragon.org/9.9/plugins/rcp-fe-lol-leagues/global/default/svg/position-bottom.svg",
    "SUP": "https://raw.communitydragon.org/9.9/plugins/rcp-fe-lol-leagues/global/default/svg/position-utility.svg",
}


def load_cards():
    with CARDS_PATH.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def iter_lol_teams(card_data):
    game = card_data.get("games", {}).get("League of Legends", {})
    for set_data in game.get("sets", {}).values():
        for league_name, league_data in set_data.get("leagues", {}).items():
            for team_name in league_data.get("teams", {}):
                yield league_name, team_name


def iter_valorant_teams(card_data):
    game = card_data.get("games", {}).get("Valorant", {})
    for set_data in game.get("sets", {}).values():
        for league_name, league_data in set_data.get("leagues", {}).items():
            for team_name in league_data.get("teams", {}):
                yield league_name, team_name


def download_image(url, path):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(response.content)


def normalize_logo(path):
    with Image.open(path) as image:
        image = image.convert("RGBA")
        image.thumbnail((256, 256), Image.LANCZOS)
        canvas = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
        x = (canvas.width - image.width) // 2
        y = (canvas.height - image.height) // 2
        canvas.alpha_composite(image, (x, y))
        canvas.save(path.with_suffix(".png"))
    if path.suffix.lower() != ".png":
        path.unlink(missing_ok=True)


def fetch_team_logos():
    card_data = load_cards()
    downloaded = 0
    skipped = 0
    missing = []

    for _league_name, team_name in sorted(set(iter_lol_teams(card_data))):
        team_slug = make_slug(team_name)
        target = TEAM_LOGO_DIR / f"{team_slug}.png"
        if target.exists():
            skipped += 1
            continue

        api_team = get_api_team(team_slug)
        image_url = (api_team or {}).get("image")
        if not image_url:
            missing.append(team_name)
            continue

        extension = source_extension(unquote(urlparse(image_url).path)) or ".png"
        temp_path = TEAM_LOGO_DIR / f"{team_slug}{extension}"
        download_image(image_url, temp_path)
        normalize_logo(temp_path)
        downloaded += 1

    return downloaded, skipped, missing


def extract_vlr_team_logo(team_url):
    page = vlr_get(team_url).text
    header_match = re.search(
        r'<div class="wf-avatar team-header-logo">(?P<body>.*?)</div>\s*</div>',
        page,
        re.IGNORECASE | re.DOTALL,
    )
    header_html = header_match.group("body") if header_match else page
    image_match = re.search(r'<img[^>]+src=["\'](?P<src>[^"\']+)["\']', header_html, re.IGNORECASE)
    if not image_match:
        return None
    return urljoin(team_url, html.unescape(image_match.group("src")))


def fetch_valorant_team_logos():
    card_data = load_cards()
    downloaded = 0
    skipped = 0
    missing = []

    for _league_name, team_name in sorted(set(iter_valorant_teams(card_data))):
        team_slug = make_slug(team_name)
        target = TEAM_LOGO_DIR / f"{team_slug}.png"
        if target.exists():
            skipped += 1
            continue

        team_url = vlr_search_team(team_name)
        if not team_url:
            missing.append(team_name)
            continue

        image_url = extract_vlr_team_logo(team_url)
        if not image_url:
            missing.append(team_name)
            continue

        extension = source_extension(unquote(urlparse(image_url).path)) or ".png"
        temp_path = TEAM_LOGO_DIR / f"{team_slug}{extension}"
        download_image(image_url, temp_path)
        normalize_logo(temp_path)
        downloaded += 1

    return downloaded, skipped, missing


def normalize_role_icon(path):
    with Image.open(path) as image:
        image = image.convert("RGBA")
        bbox = image.getbbox()
        if bbox:
            image = image.crop(bbox)
        image.thumbnail((112, 112), Image.LANCZOS)
        canvas = Image.new("RGBA", (128, 128), (0, 0, 0, 0))
        x = (canvas.width - image.width) // 2
        y = (canvas.height - image.height) // 2
        canvas.alpha_composite(image, (x, y))
        canvas.save(path)


def render_role_svg(page, role, svg_text):
    path = ROLE_ICON_DIR / f"{role.lower()}.png"
    html = f"""
    <!doctype html>
    <html>
      <head>
        <style>
          html, body {{
            background: transparent;
            height: 128px;
            margin: 0;
            overflow: hidden;
            width: 128px;
          }}
          svg {{
            display: block;
            height: 128px;
            width: 128px;
          }}
        </style>
      </head>
      <body>{svg_text}</body>
    </html>
    """
    page.set_content(html)
    page.locator("svg").screenshot(path=str(path), omit_background=True)
    normalize_role_icon(path)


def generate_role_icons():
    ROLE_ICON_DIR.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 128, "height": 128}, device_scale_factor=2)
        for role, url in ROLE_ICON_SOURCES.items():
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            render_role_svg(page, role, response.text)
        browser.close()


def main():
    generate_role_icons()
    lol_downloaded, lol_skipped, lol_missing = fetch_team_logos()
    valorant_downloaded, valorant_skipped, valorant_missing = fetch_valorant_team_logos()
    print(f"Role icons generated in {ROLE_ICON_DIR}.")
    print(f"LoL team logos downloaded: {lol_downloaded}; already present: {lol_skipped}.")
    print(f"Valorant team logos downloaded: {valorant_downloaded}; already present: {valorant_skipped}.")
    missing = lol_missing + valorant_missing
    if missing:
        print("Missing logos: " + ", ".join(missing))


if __name__ == "__main__":
    main()
