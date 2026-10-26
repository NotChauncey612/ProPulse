"""Separate group-stage and knockout layouts, populated from saved tournament state."""

from datetime import date, datetime
from zoneinfo import ZoneInfo

from . import tournament_engine as engine
from .tournament_board import Board, TEXT, MUTED, BLUE, MINT, GOLD, PANEL, LINE
from .tournament_bracket import font, knockout_matches, estimated_match_times, ROUNDS


def show_groups(event, report_day):
    return bool(event["groups"]) and date.fromisoformat(report_day) <= datetime.fromisoformat(
        event["stage_times"]["groups"]).astimezone(ZoneInfo(event["timezone"])).date()


def render_groups(event, report_day):
    count = max(map(len, event["groups"].values()))
    panel_height = 119 + count*54
    board = Board(1440, 227 + 2*(panel_height+30) + 40, font)
    matches = [m for m in event["matches"] if m["stage"] == "groups"]
    complete = all(m["winner"] for m in matches)
    day = date.fromisoformat(report_day)
    board.header(f"{day:%A} / GROUP STAGE".upper(), "The road to playoffs",
                 f"{event['game']}  /  Week of {event['week']}  /  {len(event['entrants'])} entrants",
                 "8 PLAYERS ADVANCE" if complete else "TOP TWO PER GROUP")
    board.text(48, 186, "FINAL STANDINGS" if complete else "GROUP STANDINGS", 15, MUTED, True)
    board.text(1392, 186, "SERIES RECORD / GAME DIFFERENCE", 13, MUTED, True, "ra")
    for index, group in enumerate(event["groups"]):
        x, y = 48+(index % 2)*688, 227+(index // 2)*(panel_height+30)
        board.draw.rounded_rectangle((x, y, x+656, y+panel_height), radius=18, fill=PANEL, outline=LINE)
        board.draw.rounded_rectangle((x+18, y+18, x+64, y+64), radius=12, fill="#1d3651")
        board.text(x+41, y+20, group, 29, BLUE, True, "ma")
        board.text(x+80, y+21, f"GROUP {group}", 23, TEXT, True)
        group_complete = all(m["winner"] for m in matches if m["group"] == group)
        board.text(x+635, y+29, "COMPLETE" if group_complete else "IN PROGRESS" if any(
            m["games"] for m in matches if m["group"] == group) else "UPCOMING", 13,
                   MINT if group_complete else MUTED, True, "ra")
        for offset, heading, anchor in ((24, "POS", "la"), (87, "PLAYER / SEED", "la"),
                                         (485, "W–L", "ma"), (555, "GD", "ma"), (626, "TOP 8", "ra")):
            board.text(x+offset, y+82, heading, 11, MUTED, True, anchor)
        for rank, row in enumerate(engine.standings(event, group), 1):
            ry = y+109+(rank-1)*54
            qualified = group_complete and rank <= 2
            if qualified:
                board.draw.rounded_rectangle((x+12, ry, x+644, ry+49), radius=8, fill="#143332")
                board.draw.rounded_rectangle((x+12, ry+8, x+15, ry+40), radius=1, fill=MINT)
            board.text(x+34, ry+10, f"{rank:02}", 19, MINT if qualified else MUTED, True)
            uid = row["uid"]
            board.text(x+87, ry+11, f"#{event['tournament_seeds'][uid]}", 17,
                       BLUE if qualified else MUTED, True)
            board.text(x+132, ry+7, event["entrants"][uid]["name"], 22,
                       TEXT if qualified or not group_complete else MUTED, qualified, width=300)
            board.text(x+485, ry+10, f"{row['wins']}–{row['losses']}", 20, TEXT, True, "ma")
            difference = row["game_wins"]-row["game_losses"]
            board.text(x+555, ry+12, f"{difference:+}", 17, MINT if difference > 0 else MUTED, anchor="ma")
            if qualified:
                board.draw.line((x+605, ry+25, x+612, ry+31, x+625, ry+17), fill=MINT, width=3)
            else:
                board.draw.line((x+609, ry+25, x+625, ry+25), fill=LINE, width=2)
    return board.png()


def render_knockout(event, report_day, title=None):
    zone, cutoff = ZoneInfo(event["timezone"]), date.fromisoformat(report_day)
    matches = knockout_matches(event)
    first = next((i for i, (stage, _) in enumerate(ROUNDS) if any(m["stage"] == stage for m in matches)), None)
    if first is None:
        raise ValueError("No knockout bracket has been drawn yet")
    rounds = ROUNDS[first:]
    initial = [m for m in matches if m["stage"] == rounds[0][0]]
    width = max(900, 96+484*(len(rounds)-1)+376)
    height = 271+174*(len(initial)-1)+139+70
    board = Board(width, height, font)
    finished = [label for stage, label in rounds if datetime.fromisoformat(
        event["stage_times"][stage]).astimezone(zone).date() <= cutoff and any(
        m["stage"] == stage for m in matches) and all(m["winner"] for m in matches if m["stage"] == stage)]
    status = "TOURNAMENT COMPLETE" if finished and finished[-1] == "FINAL" else (
        f"{finished[-1]} DONE" if finished else "BRACKET SET")
    board.header(f"{cutoff:%A} / KNOCKOUT STAGE".upper(), title or "Road to the final",
                 f"{event['game']}  /  Week of {event['week']}  /  Best of five", status)
    previous, nodes = [], []
    estimates = estimated_match_times(event)
    for column, (stage, heading) in enumerate(rounds):
        x = (width-376)//2 if len(rounds) == 1 else 48+column*484
        board.text(x, 193, f"{column+1:02}", 14, BLUE, True)
        board.text(x+39, 189, heading, 19, TEXT, True)
        board.draw.line((x, 229, x+376, 229), fill=LINE, width=2)
        actual = [m for m in matches if m["stage"] == stage]
        current = []
        for row in range(len(initial)//2**column):
            center = 329+174*(2**column*row+(2**column-1)/2)
            match = actual[row] if row < len(actual) else None
            if column == 0:
                participants = [match["left"], match["right"]]
                labels = [match.get(f"{side}_label", "BYE" if uid is None else event["entrants"][uid]["name"])
                          for side, uid in zip(("left", "right"), participants)]
            else:
                feeders = previous[row*2:row*2+2]
                participants = [n["winner"] for n in feeders]
                labels = [event["entrants"][uid]["name"] if uid else "To be decided" for uid in participants]
                for feeder in feeders:
                    board.draw.line(((feeder["x"]+376, feeder["center"]), (x-54, feeder["center"]),
                                     (x-54, center), (x, center)),
                                    fill="#386862" if feeder["winner"] else LINE, width=2)
            visible = match and not match.get("placeholder") and (match.get("bye") or datetime.fromisoformat(
                event["stage_times"][stage]).astimezone(zone).date() <= cutoff)
            node = {"x": x, "center": center, "uids": participants, "labels": labels,
                    "winner": match["winner"] if visible else None,
                    "scores": engine.series_score(match) if visible and not match.get("bye") else [None, None],
                    "start": estimates[stage, row]}
            current.append(node)
            nodes.append(node)
        previous = current
    for node in nodes:
        x, top = node["x"], node["center"]-58
        done = bool(node["winner"])
        board.draw.rounded_rectangle((x, top, x+376, top+139), radius=13, fill=PANEL,
                                     outline="#356458" if done else LINE)
        for dy in (51, 102):
            board.draw.line((x+13, top+dy, x+363, top+dy), fill=LINE)
        for index, uid in enumerate(node["uids"]):
            y = top+10+index*51
            won = bool(uid and uid == node["winner"])
            if won:
                board.draw.rounded_rectangle((x+1, y-5, x+4, y+37), radius=1, fill=MINT)
            if uid:
                board.draw.rounded_rectangle((x+13, y+1, x+54, y+31), radius=7, fill="#20374a")
                board.text(x+34, y+4, f"#{event['tournament_seeds'][uid]}", 14, BLUE, True, "ma")
            board.text(x+67 if uid else x+15, y, node["labels"][index], 23,
                       MINT if won else MUTED if done else TEXT, won, width=245 if uid else 300)
            score = node["scores"][index]
            board.text(x+349, y, str(score) if score is not None else "—", 23, GOLD if won else MUTED, True, "ra")
        timing = f"Est. {node['start'].astimezone(zone):%a · %I:%M %p %Z}" if node["start"] else "Bye · advances automatically"
        board.text(x+15, top+111, timing, 13, MUTED, width=280 if done else 345)
        if done:
            board.text(x+361, top+112, "DONE", 11, MINT, True, "ra")
    return board.png()
