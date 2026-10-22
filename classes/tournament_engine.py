"""Persistent weekly tournament rules, independent of Discord and user progression."""

import random
from datetime import datetime, time, timedelta, timezone
from itertools import combinations
from zoneinfo import ZoneInfo


MAX_ENTRANTS = 32
GROUP_THRESHOLD = 12
BASE_STAT = 100
GAME_INTERVAL = 30
FINALS_GAME_INTERVAL = 30 * 60
TERMINAL = {"complete", "cancelled"}


def utc_now():
    return datetime.now(timezone.utc)


def parse_time(value):
    return datetime.strptime(value, "%H:%M").time()


def week_start(now, timezone_name):
    local = now.astimezone(ZoneInfo(timezone_name))
    return local.date() - timedelta(days=local.weekday())


def new_event(config, now):
    monday = week_start(now, config["timezone"])
    zone = ZoneInfo(config["timezone"])
    start_time = parse_time(config["match_time"])

    def at(day, clock):
        return datetime.combine(monday + timedelta(days=day), clock, zone).astimezone(timezone.utc).isoformat()

    return {
        "week": monday.isoformat(),
        "game": config["game"],
        "timezone": config["timezone"],
        "channel_id": config["channel_id"],
        "opens_at": at(0, time()),
        "closes_at": at(3, time()),
        "stage_times": {"groups": at(3, start_time), "preliminary": at(4, start_time),
                        "quarterfinal": at(4, start_time), "semifinal": at(5, start_time),
                        "final": at(6, start_time)},
        "state": "registration", "entrants": {}, "groups": {}, "matches": [],
        "board_id": None, "board_dirty": True, "active_match": None, "winner": None,
    }


def due(value, now):
    return datetime.fromisoformat(value) <= now


def add_match(event, stage, left, right, group=None):
    match = {"id": len(event["matches"]) + 1, "stage": stage, "left": left,
             "right": right, "group": group, "games": [], "winner": None,
             "message_id": None, "next_game_at": None}
    event["matches"].append(match)
    return match


def bracket_pairs(seeds):
    """Seed order for a bracket with byes for the highest seeds."""
    size = 2
    while size < len(seeds):
        size *= 2
    order = [1, 2]
    while len(order) < size:
        total = len(order) * 2 + 1
        order = [item for seed in order for item in (seed, total - seed)]
    slots = [seeds[seed - 1] if seed <= len(seeds) else None for seed in order]
    return list(zip(slots[::2], slots[1::2]))


def start_knockout(event, seeds, stage):
    event["state"] = "playoffs"
    for left, right in bracket_pairs(seeds):
        match = add_match(event, stage, left, right)
        if left is None or right is None:
            match["winner"] = left or right
            match["bye"] = True


def lock_registration(event, rng=None, leaderboard_order=None):
    rng = rng or random
    seeds = list(event["entrants"])
    if leaderboard_order is None:
        rng.shuffle(seeds)
    else:
        ranked = [uid for uid in leaderboard_order if uid in event["entrants"]]
        if set(ranked) != set(seeds) or len(ranked) != len(seeds):
            raise ValueError("Leaderboard seeding must include every entrant exactly once")
        seeds = ranked
    event["tournament_seeds"] = {uid: index for index, uid in enumerate(seeds, 1)}
    if 2 <= len(seeds) < GROUP_THRESHOLD:
        size = 1 << (len(seeds) - 1).bit_length()
        bye_count = size - len(seeds)
        if bye_count:
            event["bye_recipients"] = seeds[:bye_count]
    event["seeds"] = seeds
    event["board_dirty"] = True
    if len(seeds) < 2:
        event["state"] = "cancelled"
        event["reason"] = "At least two valid teams are needed."
        return
    if len(seeds) >= GROUP_THRESHOLD:
        event["state"] = "groups"
        event["groups"] = {name: [] for name in "ABCD"}
        for index, uid in enumerate(seeds):
            # Snake seeding spreads the strongest entrants across all four groups.
            column = index % 4 if (index // 4) % 2 == 0 else 3 - index % 4
            event["groups"]["ABCD"[column]].append(uid)
        # Interleave the groups so spectators see every group during the broadcast.
        pairings = {name: list(combinations(members, 2)) for name, members in event["groups"].items()}
        for index in range(max(map(len, pairings.values()))):
            for name, pairs in pairings.items():
                if index < len(pairs):
                    add_match(event, "groups", *pairs[index], group=name)
    else:
        stage = ("preliminary" if len(seeds) > 8 else "quarterfinal" if len(seeds) > 4
                 else "semifinal" if len(seeds) > 2 else "final")
        start_knockout(event, seeds, stage)


def series_score(match):
    return [sum(game["winner"] == side for game in match["games"])
            for side in (match["left"], match["right"])]


def standings(event, group):
    rows = {uid: {"uid": uid, "wins": 0, "losses": 0, "game_wins": 0, "game_losses": 0}
            for uid in event["groups"][group]}
    for match in event["matches"]:
        if match["group"] != group or not match["winner"]:
            continue
        scores = series_score(match)
        for index, uid in enumerate((match["left"], match["right"])):
            row = rows[uid]
            row["wins"] += int(match["winner"] == uid)
            row["losses"] += int(match["winner"] != uid)
            row["game_wins"] += scores[index]
            row["game_losses"] += scores[1 - index]
    # Ties: series wins, game differential, game wins, then the frozen tournament seed.
    return sorted(rows.values(), key=lambda row: (
        -row["wins"], -(row["game_wins"] - row["game_losses"]),
        -row["game_wins"], event["seeds"].index(row["uid"])))


def advance_stage(event):
    """Advance only after every series in the current stage is decided."""
    if event["state"] in TERMINAL or event["state"] == "registration":
        return
    stage = event["matches"][-1]["stage"]
    matches = [match for match in event["matches"] if match["stage"] == stage]
    if not all(match["winner"] for match in matches):
        return
    if stage == "groups":
        ranks = {group: standings(event, group) for group in "ABCD"}
        # A1-B2 / C1-D2 / B1-A2 / D1-C2: group opponents cannot meet before the final.
        for a, ai, b, bi in (("A", 0, "B", 1), ("C", 0, "D", 1),
                             ("B", 0, "A", 1), ("D", 0, "C", 1)):
            add_match(event, "quarterfinal", ranks[a][ai]["uid"], ranks[b][bi]["uid"])
        event["state"] = "playoffs"
    elif stage == "final":
        event["winner"] = matches[0]["winner"]
        event["state"] = "complete"
    else:
        next_stage = {"preliminary": "quarterfinal", "quarterfinal": "semifinal", "semifinal": "final"}[stage]
        winners = [match["winner"] for match in matches]
        for left, right in zip(winners[::2], winners[1::2]):
            add_match(event, next_stage, left, right)


def next_match(event, now):
    if event["state"] in TERMINAL or event["state"] == "registration":
        return None
    advance_stage(event)
    for match in event["matches"]:
        if not match["winner"] and due(event["stage_times"][match["stage"]], now):
            return match
    return None


def win_chance(left_power, right_power):
    a, b = max(1, left_power) ** 2, max(1, right_power) ** 2
    return a / (a + b)


def pending_daily_recaps(event):
    """A day's picture is ready only after all its rounds and game posts finish."""
    if event["state"] in {"registration", "cancelled"}:
        return []
    zone = ZoneInfo(event["timezone"])
    days = {}
    for match in event["matches"]:
        day = datetime.fromisoformat(event["stage_times"][match["stage"]]).astimezone(zone).date().isoformat()
        days.setdefault(day, []).append(match)
    posted = event.get("daily_recaps", {})
    return [day for day, matches in sorted(days.items())
            if day not in posted and any(match["games"] for match in matches)
            and all(match["winner"] for match in matches)
            and all(game.get("message_id") for match in matches for game in match["games"])]


def tournament_prizes(event):
    if event["state"] != "complete":
        return []
    final = next(match for match in event["matches"] if match["stage"] == "final")
    winner = final["winner"]
    runner_up = final["right"] if final["left"] == winner else final["left"]
    prizes = [{"user_id": winner, "place": 1, "gold": 20, "cash": 500},
              {"user_id": runner_up, "place": 2, "gold": 10, "cash": 250}]
    for match in event["matches"]:
        if match["stage"] == "semifinal" and not match.get("bye"):
            loser = match["right"] if match["left"] == match["winner"] else match["left"]
            prizes.append({"user_id": loser, "place": 3, "gold": 5, "cash": 150})
    return prizes


def play_game(event, match, now, interval, rng=None):
    """Persist one independent game; first to three ends the BO5 immediately."""
    if match["winner"]:
        raise ValueError("This series is already decided.")
    rng = rng or random
    left = event["entrants"][match["left"]]
    right = event["entrants"][match["right"]]
    probability = win_chance(left["power"], right["power"])
    winner = match["left"] if rng.random() < probability else match["right"]
    match["games"].append({"winner": winner, "played_at": now.isoformat(),
                           "left_power": left["power"], "right_power": right["power"]})
    match["dirty"] = True
    event["board_dirty"] = True
    match["next_game_at"] = (now + timedelta(seconds=interval)).isoformat()
    if max(series_score(match)) == 3:
        match["winner"] = winner
        event["active_match"] = None
        event["next_match_at"] = (now + timedelta(seconds=interval)).isoformat()
        advance_stage(event)
    return winner
