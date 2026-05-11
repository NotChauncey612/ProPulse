import argparse
import json
import os
import re
from pathlib import Path
from urllib.parse import quote, unquote, urljoin, urlparse

import requests
from playwright.sync_api import sync_playwright


CARDS_PATH = Path("data/cards.json")
SAVE_DIR = Path("player_images")
SET_NAME = "LOL '26"
LOLESPORTS_TEAM_BASE = "https://lolesports.com/teams"
LOLESPORTS_API_BASE = "https://esports-api.lolesports.com/persisted/gw"
LOLESPORTS_FEED_BASE = "https://feed.lolesports.com/livestats/v1/window"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/NotChauncey612/ProPulse/main"
LOLESPORTS_API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}
CARD_ROLES = ["TOP", "JNG", "MID", "BOT", "SUP"]
API_ROLE_TO_CARD_ROLE = {
    "top": "TOP",
    "jungle": "JNG",
    "mid": "MID",
    "middle": "MID",
    "bottom": "BOT",
    "bot": "BOT",
    "support": "SUP",
}
LEAGUE_IDS = {
    "LCS": "98767991299243165",
    "CBLOL": "98767991332355509",
    "LEC": "98767991302996019",
    "LCK": "98767991310872058",
    "LPL": "98767991314006698",
    "LCP": "113476371197627891",
}

TEAM_SLUG_ALIASES = {
    "geng": "gen-g",
    "mvk-esports": "mvke",
    "team-heretics-lec": "team-heretics",
    "lyon-gaming": "lyon",
    "ground-zero": "ground-zero-gaming",
    "saigon-buffalo-esports": "mvke",
}

TEAM_URL_SLUG_FALLBACKS = {
    "bnk-fearx": ["bnk-fearx", "fearx"],
    "dn-soopers": ["dn-soopers", "kwangdong-freecs"],
    "dplus-kia": ["dplus-kia", "dwg-kia"],
    "gen-g": ["gen-g", "geng"],
    "giantx": ["giantx-lec", "giantx"],
    "hanjin-brion": ["hanjin-brion", "fredit-brion"],
    "kiwoom-drx": ["kiwoom-drx", "drx"],
    "movistar-koi": ["mad-lions", "movistar-koi"],
    "ninjas-in-pyjamas": ["shenzen-ninjas-in-pyjamas", "ninjas-in-pyjamas"],
    "red-canids": ["red-kalunga", "red-canids"],
    "shifters": ["shifters", "team-bds"],
    "team-heretics": ["team-heretics", "team-heretics-lec"],
    "vivo-keyd-stars": ["vivo-keyd", "vivo-keyd-stars"],
    "lyon": ["lyon", "lyon-gaming"],
    "ground-zero-gaming": ["ground-zero-gaming", "ground-zero"],
    "mvke": ["mvke", "mvk-esports", "saigon-buffalo-esports"],
}


def clean_filename(text):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", text)


def make_slug(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-")


def normalize_match_text(text):
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def github_raw_url(filepath):
    path = Path(filepath).as_posix()
    encoded_path = quote(path, safe="/")
    return f"{GITHUB_RAW_BASE}/{encoded_path}"


def load_cards():
    with CARDS_PATH.open("r", encoding="utf-8") as f:
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

    with CARDS_PATH.open("w", encoding="utf-8") as f:
        f.write("\n".join(lines))


def iter_card_contexts(card_data):
    for game in card_data["games"].values():
        for set_data in game["sets"].values():
            for league_name, league_data in set_data["leagues"].items():
                for team_name, team_data in league_data["teams"].items():
                    for card in team_data["cards"]:
                        yield league_name, team_name, card


def iter_team_contexts(card_data):
    for game in card_data["games"].values():
        for set_data in game["sets"].values():
            for league_name, league_data in set_data["leagues"].items():
                for team_name, team_data in league_data["teams"].items():
                    yield league_name, team_name, team_data


def load_valid_players_by_team(card_data):
    teams = {}

    for _, team_name, card in iter_card_contexts(card_data):
        team_key = make_slug(team_name)
        teams.setdefault(team_key, set()).add(card["ign"].lower())

    return teams


def get_card_info(card_data, team_slug, player_ign):
    lookup_slug = TEAM_SLUG_ALIASES.get(team_slug, team_slug)

    for league_name, team_name, card in iter_card_contexts(card_data):
        if make_slug(team_name) != lookup_slug:
            continue

        if card["ign"].lower() == player_ign.lower():
            return {
                "league": league_name,
                "team": team_name,
                "card": card,
            }

    return None


def update_card_image(card_data, team_slug, player_ign, image_url):
    info = get_card_info(card_data, team_slug, player_ign)

    if not info:
        return False

    info["card"]["image"] = image_url
    return True


def card_local_path(league_name, team_name, team_slug, player_ign, extension=".png"):
    return (
        SAVE_DIR
        / SET_NAME
        / league_name
        / clean_filename(team_name)
        / f"{team_slug}_{clean_filename(player_ign.lower())}{extension}"
    )


def source_extension(url):
    path = unquote(urlparse(url).path)
    extension = Path(path).suffix.lower()

    if extension in IMAGE_EXTENSIONS:
        return extension

    return ".png"


def card_id_player_slug(text):
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "", text)
    return text or "unknown"


def lolesports_get(path, params=None):
    headers = {"x-api-key": LOLESPORTS_API_KEY}
    response = requests.get(
        f"{LOLESPORTS_API_BASE}/{path}",
        headers=headers,
        params={"hl": "en-US", **(params or {})},
        timeout=30,
    )
    response.raise_for_status()
    return response.json()


def feed_window(game_id):
    response = requests.get(f"{LOLESPORTS_FEED_BASE}/{game_id}", timeout=30)
    response.raise_for_status()
    return response.json()


def get_api_team(team_slug):
    for candidate_url in team_url_candidates(team_slug):
        candidate_slug = candidate_url.rstrip("/").split("/")[-1]
        data = lolesports_get("getTeams", {"id": candidate_slug})
        teams = data.get("data", {}).get("teams", [])

        if teams:
            return teams[0]

    return None


def normalized_team_name(text):
    text = text.lower()
    text = text.replace("alienware", "")
    text = text.replace("kia", "")
    return normalize_match_text(text)


def event_mentions_team(event, team):
    wanted = {
        normalized_team_name(team.get("name", "")),
        normalize_match_text(team.get("code", "")),
    }
    wanted.discard("")

    for event_team in event.get("match", {}).get("teams", []):
        seen = {
            normalized_team_name(event_team.get("name", "")),
            normalize_match_text(event_team.get("code", "")),
        }

        for value in wanted:
            if value in seen or any(value and value in item for item in seen):
                return True

    return False


def event_details(match_id):
    return lolesports_get("getEventDetails", {"id": match_id})


def latest_completed_event_for_team(league_name, team):
    league_id = LEAGUE_IDS.get(league_name)

    if not league_id:
        return None

    data = lolesports_get("getSchedule", {"leagueId": league_id})
    events = data.get("data", {}).get("schedule", {}).get("events", [])
    completed = [
        event for event in events
        if event.get("state") == "completed"
        and event.get("type") == "match"
        and event_mentions_team(event, team)
    ]

    for event in sorted(
        completed,
        key=lambda item: item.get("startTime", ""),
        reverse=True,
    ):
        match_id = event.get("match", {}).get("id")

        if not match_id:
            continue

        try:
            details = event_details(match_id)
        except Exception as e:
            print(f"  Could not read event details for {match_id}: {e}")
            continue

        detail_teams = details["data"]["event"]["match"].get("teams", [])

        if any(item.get("id") == team.get("id") for item in detail_teams):
            event["_details"] = details
            return event

    return None


def completed_games_for_event(event):
    data = event.get("_details")

    if not data:
        match_id = event.get("match", {}).get("id")

        if not match_id:
            return []

        data = event_details(match_id)

    return [
        game for game in data["data"]["event"]["match"]["games"]
        if game.get("state") == "completed"
    ]


def last_match_players(league_name, team):
    event = latest_completed_event_for_team(league_name, team)

    if not event:
        return []

    games = completed_games_for_event(event)

    if not games:
        return []

    last_game = max(games, key=lambda game: game.get("number", 0))
    team_game = next(
        (
            item for item in last_game.get("teams", [])
            if item.get("id") == team.get("id")
        ),
        None,
    )

    if not team_game:
        return []

    side = team_game["side"]
    try:
        metadata = feed_window(last_game["id"]).get("gameMetadata", {})
    except Exception as e:
        print(f"  Could not read game feed for {last_game['id']}: {e}")
        return []
    participants = metadata.get(f"{side}TeamMetadata", {}).get(
        "participantMetadata",
        [],
    )

    roster_by_id = {
        player.get("id"): player
        for player in team.get("players", [])
    }
    selected = []

    for participant in participants:
        role = API_ROLE_TO_CARD_ROLE.get(participant.get("role", "").lower())

        if not role:
            continue

        api_player = roster_by_id.get(participant.get("esportsPlayerId"), {})
        summoner_name = participant.get("summonerName", "")
        ign = re.sub(r"^[A-Z0-9]{2,5}\s+", "", summoner_name).strip()

        selected.append({
            "ign": api_player.get("summonerName") or ign,
            "role": role,
            "image": api_player.get("image", ""),
            "champion": participant.get("championId", ""),
            "player_id": participant.get("esportsPlayerId", ""),
        })

    return sorted(
        selected,
        key=lambda player: CARD_ROLES.index(player["role"]),
    )


def api_team_players(team):
    selected = []

    for player in team.get("players", []):
        role = API_ROLE_TO_CARD_ROLE.get(player.get("role", "").lower())
        ign = player.get("summonerName", "").strip()

        if not role or not ign:
            continue

        selected.append({
            "ign": ign,
            "role": role,
            "image": player.get("image", ""),
            "champion": "",
            "player_id": player.get("id", ""),
        })

    return sorted(
        selected,
        key=lambda player: CARD_ROLES.index(player["role"]),
    )


def existing_card_prefix(cards):
    if not cards:
        return "2026_lol_unknown_"

    card_id = cards[0].get("card_id", "")
    return re.sub(r"[^_]+$", "", card_id)


def sync_team_cards_from_last_match(card_data, league_name, team_name, team_slug):
    api_team = get_api_team(team_slug)

    if not api_team:
        print(f"No API team found for {team_name} ({team_slug})")
        return 0

    players = api_team_players(api_team)

    if len(players) != 5:
        players = last_match_players(league_name, api_team)

    if len(players) != 5:
        print(f"No five-player last-match lineup found for {team_name}")
        return 0

    league_data = None
    team_data = None

    for game in card_data["games"].values():
        for set_data in game["sets"].values():
            league_data = set_data["leagues"].get(league_name)

            if league_data and team_name in league_data["teams"]:
                team_data = league_data["teams"][team_name]
                break

        if team_data:
            break

    if not team_data:
        return 0

    prefix = existing_card_prefix(team_data["cards"])
    existing_images = {
        (card.get("role"), card.get("ign", "").lower()): card.get("image", "")
        for card in team_data["cards"]
    }
    new_cards = []

    for player in players:
        image_url = existing_images.get(
            (player["role"], player["ign"].lower()),
            "",
        )

        if player["image"] and "placeholder" not in player["image"].lower():
            extension = source_extension(player["image"])
            filepath = card_local_path(
                league_name,
                team_name,
                team_slug,
                player["ign"],
                extension,
            )

            try:
                download_image(player["image"], filepath)
                image_url = github_raw_url(filepath)
            except Exception as e:
                print(f"  Could not download {team_name} {player['ign']}: {e}")

        new_cards.append({
            "card_id": f"{prefix}{card_id_player_slug(player['ign'])}",
            "ign": player["ign"],
            "team": team_name,
            "role": player["role"],
            "league": league_name,
            "set": "LoL '26",
            "image": image_url,
        })

    team_data["cards"] = new_cards
    print(
        f"Synced {league_name} - {team_name}: "
        + ", ".join(f"{p['role']} {p['ign']}" for p in players)
    )
    return 5


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


def scrape_images(team_url, headless=True):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        page.goto(team_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        images = page.evaluate(
            """
            () => {
                const results = [];

                document.querySelectorAll("img").forEach(img => {
                    if (img.src) {
                        results.push({
                            type: "img",
                            src: img.src,
                            text: [
                                img.alt || "",
                                img.title || "",
                                img.getAttribute("aria-label") || ""
                            ].join(" ")
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
                                text: [
                                    el.innerText || "",
                                    el.getAttribute("aria-label") || "",
                                    el.getAttribute("title") || ""
                                ].join(" ")
                            });
                        }
                    }
                });

                return results;
            }
            """
        )

        browser.close()
        return images


def print_missing_images(card_data):
    total = 0
    missing_total = 0

    print("\nMissing image links:")

    for game in card_data["games"].values():
        for set_data in game["sets"].values():
            for league_name, league_data in set_data["leagues"].items():
                for team_name, team_data in league_data["teams"].items():
                    missing = [
                        card for card in team_data["cards"]
                        if not card.get("image")
                    ]

                    total += len(team_data["cards"])
                    missing_total += len(missing)

                    if missing:
                        print(f"\n{league_name} - {team_name}")

                        for card in missing:
                            print(f"  - {card['ign']} ({card['role']})")

    if not missing_total:
        print("None. Every card has an image.")

    print(f"\nImage coverage: {total - missing_total}/{total}")


def extract_image_haystack(image):
    src = unquote(image["src"])
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


def find_player_match(image, team_players):
    haystack = extract_image_haystack(image)

    for ign in sorted(team_players, key=len, reverse=True):
        normalized_ign = normalize_match_text(ign)

        if len(normalized_ign) <= 2:
            pattern = rf"(^|[^a-z0-9]){re.escape(normalized_ign)}([^a-z0-9]|$)"
            raw_text = unquote(f"{image.get('text', '')} {image.get('src', '')}").lower()

            if re.search(pattern, raw_text):
                return ign

            continue

        if normalized_ign in haystack:
            return ign

    return None


def image_is_player_asset(src):
    src_lower = unquote(src).lower()

    return (
        "/players/" in src_lower
        or "%2fplayers%2f" in src_lower
        or "/player/" in src_lower
        or "%2fplayer%2f" in src_lower
    )


def team_url_candidates(team_slug):
    slugs = TEAM_URL_SLUG_FALLBACKS.get(team_slug, [team_slug])
    return [f"{LOLESPORTS_TEAM_BASE}/{slug}" for slug in slugs]


def team_slugs_from_cards(card_data):
    return [
        make_slug(team_name)
        for _, team_name, _ in iter_card_contexts(card_data)
    ]


def link_existing_local_images(card_data):
    linked = 0

    for league_name, team_name, card in iter_card_contexts(card_data):
        if card.get("image"):
            continue

        team_slug = make_slug(team_name)
        player_slug = clean_filename(card["ign"].lower())
        team_folder = SAVE_DIR / SET_NAME / league_name / clean_filename(team_name)

        for extension in IMAGE_EXTENSIONS:
            filepath = team_folder / f"{team_slug}_{player_slug}{extension}"

            if filepath.exists():
                card["image"] = github_raw_url(filepath)
                linked += 1
                break

    return linked


def scrape_team(card_data, team_slug, team_players, headless=True):
    for team_url in team_url_candidates(team_slug):
        print(f"\nScraping {team_url}")

        try:
            images = scrape_images(team_url, headless=headless)
        except Exception as e:
            print(f"  Could not scrape {team_url}: {e}")
            continue

        saved = 0

        for index, image in enumerate(images):
            src = image["src"]

            if not src:
                continue

            if src.startswith("//"):
                src = "https:" + src

            src = urljoin(team_url, src)
            image["src"] = src

            if not image_is_player_asset(src):
                continue

            player_match = find_player_match(image, team_players)

            if not player_match:
                continue

            info = get_card_info(card_data, team_slug, player_match)

            if not info:
                continue

            extension = source_extension(src)
            filepath = card_local_path(
                info["league"],
                info["team"],
                team_slug,
                player_match,
                extension,
            )

            try:
                download_image(src, filepath)

                github_url = github_raw_url(filepath)
                update_card_image(card_data, team_slug, player_match, github_url)

                saved += 1
                print(f"  Saved {filepath}")

            except Exception as e:
                print(f"  Skipped image {index} ({src}): {e}")

        if saved:
            return saved

    return 0


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download player images and update data/cards.json."
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print missing card images without downloading or rewriting cards.json.",
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
        "--sync-rosters",
        action="store_true",
        help=(
            "Replace each selected team's cards with the five players from "
            "that team's most recent completed match."
        ),
    )
    return parser.parse_args()


def main():
    args = parse_args()
    card_data = load_cards()

    if args.report_only:
        print_missing_images(card_data)
        return

    SAVE_DIR.mkdir(exist_ok=True)

    linked = link_existing_local_images(card_data)

    if linked:
        print(f"Linked {linked} existing local image(s).")

    valid_players_by_team = load_valid_players_by_team(card_data)
    requested_teams = {make_slug(team) for team in args.team}
    teams_to_scrape = sorted(set(team_slugs_from_cards(card_data)))

    if args.sync_rosters:
        for league_name, team_name, _ in iter_team_contexts(card_data):
            team_slug = make_slug(team_name)

            if requested_teams and team_slug not in requested_teams:
                continue

            sync_team_cards_from_last_match(
                card_data,
                league_name,
                team_name,
                team_slug,
            )

        dump_cards_json(card_data)
        print_missing_images(card_data)
        print("\ncards.json updated from last-match rosters.")
        return

    for team_slug in teams_to_scrape:
        if requested_teams and team_slug not in requested_teams:
            continue

        lookup_slug = TEAM_SLUG_ALIASES.get(team_slug, team_slug)
        team_players = valid_players_by_team.get(lookup_slug, set())

        if not team_players:
            print(f"Team not found in cards.json: {team_slug}")
            continue

        missing_players = {
            ign.lower()
            for _, team_name, card in iter_card_contexts(card_data)
            if make_slug(team_name) == lookup_slug and not card.get("image")
            for ign in [card["ign"]]
        }

        if not missing_players:
            continue

        scrape_team(
            card_data,
            team_slug,
            missing_players,
            headless=not args.show_browser,
        )

    dump_cards_json(card_data)
    print_missing_images(card_data)
    print("\ncards.json updated with GitHub image URLs.")


if __name__ == "__main__":
    main()
