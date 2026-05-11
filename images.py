import os
import re
import json
import requests
from urllib.parse import urljoin, quote
from playwright.sync_api import sync_playwright

TEAM_URLS = [

    # LCK
    "https://lolesports.com/teams/fearx",
    "https://lolesports.com/teams/kwangdong-freecs",
    "https://lolesports.com/teams/dwg-kia",
    "https://lolesports.com/teams/geng",
    "https://lolesports.com/teams/fredit-brion",
    "https://lolesports.com/teams/hanwha-life-esports",
    "https://lolesports.com/teams/drx",
    "https://lolesports.com/teams/kt-rolster",
    "https://lolesports.com/teams/nongshim-redforce",
    "https://lolesports.com/teams/t1",

    # LEC
    "https://lolesports.com/teams/fnatic",
    "https://lolesports.com/teams/g2-esports",
    "https://lolesports.com/teams/giantx",
    "https://lolesports.com/teams/mad-lions",
    "https://lolesports.com/teams/movistar-koi",
    "https://lolesports.com/teams/natus-vincere",
    "https://lolesports.com/teams/team-bds",
    "https://lolesports.com/teams/sk-gaming",
    "https://lolesports.com/teams/team-heretics-lec",
    "https://lolesports.com/teams/team-vitality",

    # LCS
    "https://lolesports.com/teams/cloud9",
    "https://lolesports.com/teams/dignitas",
    "https://lolesports.com/teams/disguised",
    "https://lolesports.com/teams/flyquest",
    "https://lolesports.com/teams/lyon-gaming",
    "https://lolesports.com/teams/sentinels",
    "https://lolesports.com/teams/shopify-rebellion",
    "https://lolesports.com/teams/team-liquid",

    # LPL
    "https://lolesports.com/teams/bilibili-gaming",
    "https://lolesports.com/teams/jd-gaming",
    "https://lolesports.com/teams/top-esports",
    "https://lolesports.com/teams/lng-esports",
    "https://lolesports.com/teams/weibo-gaming",
    "https://lolesports.com/teams/royal-never-give-up",

    # LCP
    "https://lolesports.com/teams/ctbc-flying-oyster",
    "https://lolesports.com/teams/detonation-focusme",
    "https://lolesports.com/teams/deep-cross-gaming",
    "https://lolesports.com/teams/fukuoka-softbank-hawks-gaming",
    "https://lolesports.com/teams/gam-esports",
    "https://lolesports.com/teams/ground-zero",
    "https://lolesports.com/teams/saigon-buffalo-esports",
    "https://lolesports.com/teams/team-secret-whales",

    # CBLOL
    "https://lolesports.com/teams/loud",
    "https://lolesports.com/teams/pain-gaming",
    "https://lolesports.com/teams/furia",
    "https://lolesports.com/teams/red-canids",
    "https://lolesports.com/teams/vivo-keyd-stars",
    "https://lolesports.com/teams/fluxo",
    "https://lolesports.com/teams/intz",
    "https://lolesports.com/teams/liberty",
]

CARDS_PATH = "data/cards.json"

SAVE_DIR = "player_images"

SET_NAME = "LOL '26"

GITHUB_RAW_BASE = (
    "https://raw.githubusercontent.com/"
    "NotChauncey612/ProPulse/main"
)

os.makedirs(SAVE_DIR, exist_ok=True)


TEAM_SLUG_ALIASES = {
    "geng": "gen-g",
    "mvk-esports": "mvke",
}


def clean_filename(text):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)


def make_slug(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def github_raw_url(filepath):
    path = filepath.replace("\\", "/")
    encoded_path = quote(path, safe="/")

    return f"{GITHUB_RAW_BASE}/{encoded_path}"


def load_cards():
    with open(CARDS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def card_to_one_line(card, indent_level):
    indent = " " * indent_level
    return indent + json.dumps(card, ensure_ascii=False)


def dump_cards_json(data):
    lines = []

    lines.append("{")
    lines.append('  "games": {')

    game_items = list(data["games"].items())

    for game_index, (game_name, game_data) in enumerate(game_items):
        lines.append(f'    "{game_name}": {{')
        lines.append('      "sets": {')

        set_items = list(game_data["sets"].items())

        for set_index, (set_name, set_data) in enumerate(set_items):
            lines.append(f'        "{set_name}": {{')
            lines.append('          "leagues": {')

            league_items = list(set_data["leagues"].items())

            for league_index, (league_name, league_data) in enumerate(league_items):
                lines.append(f'            "{league_name}": {{')
                lines.append('              "teams": {')

                team_items = list(league_data["teams"].items())

                for team_index, (team_name, team_data) in enumerate(team_items):
                    lines.append(f'                "{team_name}": {{')
                    lines.append('                  "cards": [')

                    cards = team_data["cards"]

                    for card_index, card in enumerate(cards):
                        line = card_to_one_line(card, 20)

                        if card_index < len(cards) - 1:
                            line += ","

                        lines.append(line)

                    lines.append("                  ]")

                    team_close = "                }"

                    if team_index < len(team_items) - 1:
                        team_close += ","

                    lines.append(team_close)

                lines.append("              }")

                league_close = "            }"

                if league_index < len(league_items) - 1:
                    league_close += ","

                lines.append(league_close)

            lines.append("          }")

            set_close = "        }"

            if set_index < len(set_items) - 1:
                set_close += ","

            lines.append(set_close)

        lines.append("      }")

        game_close = "    }"

        if game_index < len(game_items) - 1:
            game_close += ","

        lines.append(game_close)

    lines.append("  }")
    lines.append("}")

    with open(CARDS_PATH, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def load_valid_players_by_team(card_data):
    teams = {}

    for game in card_data["games"].values():
        for set_data in game["sets"].values():
            for league_data in set_data["leagues"].values():
                for team_name, team_data in league_data["teams"].items():
                    team_key = make_slug(team_name)

                    teams[team_key] = set()

                    for card in team_data["cards"]:
                        teams[team_key].add(card["ign"].lower())

    return teams


def get_card_info(card_data, team_slug, player_ign):
    lookup_slug = TEAM_SLUG_ALIASES.get(team_slug, team_slug)

    for game in card_data["games"].values():
        for set_data in game["sets"].values():
            for league_name, league_data in set_data["leagues"].items():
                for team_name, team_data in league_data["teams"].items():

                    if make_slug(team_name) != lookup_slug:
                        continue

                    for card in team_data["cards"]:
                        if card["ign"].lower() == player_ign.lower():

                            return {
                                "league": league_name,
                                "team": team_name,
                                "card": card
                            }

    return None


def update_card_image(card_data, team_slug, player_ign, image_url):
    info = get_card_info(card_data, team_slug, player_ign)

    if not info:
        return False

    info["card"]["image"] = image_url

    return True


def download_image(url, filepath):
    response = requests.get(url)
    response.raise_for_status()

    with open(filepath, "wb") as f:
        f.write(response.content)


def scrape_images(team_url):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)

        page = browser.new_page()

        page.goto(
            team_url,
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(8000)

        images = page.evaluate("""
        () => {
            const results = [];

            document.querySelectorAll("img").forEach(img => {
                if (img.src) {
                    results.push({
                        type: "img",
                        src: img.src,
                        text: img.alt || ""
                    });
                }
            });

            document.querySelectorAll("*").forEach(el => {
                const style = window.getComputedStyle(el);
                const bg = style.backgroundImage;

                if (bg && bg !== "none") {
                    const match = bg.match(/url\\(["']?(.*?)["']?\\)/);

                    if (match && match[1]) {
                        results.push({
                            type: "background",
                            src: match[1],
                            text:
                                el.innerText ||
                                el.getAttribute("aria-label") ||
                                ""
                        });
                    }
                }
            });

            return results;
        }
        """)

        browser.close()

        return images


def print_missing_images(card_data):
    print("\nMissing image links:")

    found_missing = False

    for game in card_data["games"].values():
        for set_data in game["sets"].values():
            for league_name, league_data in set_data["leagues"].items():
                for team_name, team_data in league_data["teams"].items():

                    missing = [
                        card for card in team_data["cards"]
                        if not card.get("image")
                    ]

                    if missing:
                        found_missing = True

                        print(f"\n{league_name} - {team_name}")

                        for card in missing:
                            print(
                                f"  - {card['ign']} ({card['role']})"
                            )

    if not found_missing:
        print("None. Every card has an image.")


card_data = load_cards()

VALID_PLAYERS_BY_TEAM = load_valid_players_by_team(card_data)

for team_url in TEAM_URLS:

    team_slug = team_url.rstrip("/").split("/")[-1]

    lookup_slug = TEAM_SLUG_ALIASES.get(
        team_slug,
        team_slug
    )

    team_players = VALID_PLAYERS_BY_TEAM.get(
        lookup_slug,
        set()
    )

    if not team_players:
        print(f"Team not found in cards.json: {team_slug}")
        continue

    images = scrape_images(team_url)

    for index, image in enumerate(images):

        src = image["src"]

        if not src:
            continue

        if src.startswith("//"):
            src = "https:" + src

        src = urljoin(team_url, src)

        src_lower = src.lower()

        if (
            "/players/" not in src_lower and
            "%2fplayers%2f" not in src_lower
        ):
            continue

        text = image["text"] or f"player_{index}"

        text_lower = text.lower()

        player_match = None

        for ign in team_players:
            if ign in text_lower:
                player_match = ign
                break

        if not player_match:
            continue

        info = get_card_info(
            card_data,
            team_slug,
            player_match
        )

        if not info:
            continue

        league_name = info["league"]
        team_name = info["team"]

        folder_path = os.path.join(
            SAVE_DIR,
            SET_NAME,
            league_name,
            clean_filename(team_name)
        )

        os.makedirs(folder_path, exist_ok=True)

        filename = (
            f"{team_slug}_"
            f"{clean_filename(player_match)}.png"
        )

        filepath = os.path.join(
            folder_path,
            filename
        )

        try:
            download_image(src, filepath)

            github_url = github_raw_url(filepath)

            update_card_image(
                card_data,
                team_slug,
                player_match,
                github_url
            )

            print(f"Saved {filepath}")

        except Exception as e:
            print(f"Skipped {src}: {e}")

dump_cards_json(card_data)

print_missing_images(card_data)

print("\ncards.json updated with GitHub image URLs.")