"""Render public tournament bracket pictures from saved results using Pillow."""

import io
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont

from . import tournament_engine as engine


ROUNDS = [("preliminary", "PRELIMINARIES"), ("quarterfinal", "QUARTERFINALS"),
          ("semifinal", "SEMIFINALS"), ("final", "FINAL")]
BG = "#101722"
PANEL = "#1b2635"
TEXT = "#eef3fa"
MUTED = "#9caec3"
ACCENT = "#61dfb5"
GOLD = "#f0cb72"


def font(size, bold=False):
    candidates = (["C:/Windows/Fonts/segoeuib.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"]
                  if bold else ["C:/Windows/Fonts/segoeui.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"])
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def knockout_matches(event):
    """Show group qualification slots until the real quarterfinals can be drawn."""
    matches = list(event["matches"])
    if not any(match["stage"] != "groups" for match in matches) and event["groups"]:
        for a, b in (("A", "B"), ("C", "D"), ("B", "A"), ("D", "C")):
            matches.append({"stage": "quarterfinal", "left": None, "right": None,
                            "left_label": f"Group {a} winner", "right_label": f"Group {b} runner-up",
                            "winner": None, "games": [], "placeholder": True})
    return matches


def estimated_match_times(event):
    """Estimate serial start times assuming each BO5 lasts the full five games."""
    matches = knockout_matches(event)
    first = next(stage for stage, _ in ROUNDS if any(m["stage"] == stage for m in matches))
    first_column = next(i for i, item in enumerate(ROUNDS) if item[0] == first)
    initial_count = sum(m["stage"] == first for m in matches)
    times, cursors = {}, {}
    zone = ZoneInfo(event["timezone"])
    for column, (stage, _) in enumerate(ROUNDS[first_column:]):
        scheduled = datetime.fromisoformat(event["stage_times"][stage])
        day = scheduled.astimezone(zone).date()
        cursor = max(scheduled, cursors.get(day, scheduled))
        actual = [m for m in matches if m["stage"] == stage]
        for row in range(initial_count // 2 ** column):
            if row < len(actual) and actual[row].get("bye"):
                times[stage, row] = None
                continue
            times[stage, row] = cursor
            # Early rounds wait 30 seconds before game one; weekend rounds reveal it immediately.
            duration = (4 * engine.FINALS_GAME_INTERVAL if stage in {"semifinal", "final"}
                        else 5 * engine.GAME_INTERVAL)
            cursor += timedelta(seconds=duration + engine.GAME_INTERVAL)
        cursors[day] = cursor
    return times


def render_bracket(event, report_day, title=None):
    """Return a PNG buffer; future rounds show feeder labels rather than future results."""
    zone = ZoneInfo(event["timezone"])
    cutoff = date.fromisoformat(report_day)
    matches = knockout_matches(event)
    rounds = [(key, label) for key, label in ROUNDS if any(m["stage"] == key for m in matches)]
    if not rounds:
        raise ValueError("No knockout bracket has been drawn yet")
    first = rounds[0][0]
    rounds = ROUNDS[next(i for i, item in enumerate(ROUNDS) if item[0] == first):]
    initial = [m for m in matches if m["stage"] == first]
    width = max(1100 if event["groups"] else 900, 80 + len(rounds) * 350)
    bracket_height = max(250, len(initial) * 112 + 100)
    group_height = 110 + max(map(len, event["groups"].values())) * 32 if event["groups"] else 0
    champion_visible = bool(event.get("winner")) and datetime.fromisoformat(
        event["stage_times"]["final"]).astimezone(zone).date() <= cutoff
    height = 160 + bracket_height + group_height + (80 if champion_visible else 20)
    canvas = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(canvas)

    def text(x, y, value, size=18, color=TEXT, bold=False, max_width=None):
        face = font(size, bold)
        value = str(value).replace("\n", " ").replace("\r", " ")
        if max_width:
            original = value
            while value and draw.textlength(value, font=face) > max_width:
                original = original[:-1]
                value = original + "…"
        draw.text((x, y), value, font=face, fill=color)

    def name(uid):
        label = event["entrants"].get(uid, {}).get("name", "BYE" if uid is None else "TBD")
        seed = event.get("tournament_seeds", {}).get(uid)
        return f"#{seed} {label}" if seed else label

    def stage_finished_by_day(stage):
        return datetime.fromisoformat(event["stage_times"][stage]).astimezone(zone).date() <= cutoff

    text(40, 25, "PROPULSE  /  WEEKLY TOURNAMENT", 16, ACCENT, True)
    text(40, 55, title or f"{cutoff:%A} results & bracket", 32, TEXT, True)
    text(40, 105, f"{event['game']}   •   Week of {event['week']}   •   {len(event['entrants'])} entrants   •   Best of five", 17, MUTED)
    nodes = []
    previous = []
    estimates = estimated_match_times(event)
    for column, (stage, label) in enumerate(rounds):
        x = 40 + column * 350
        text(x, 160, label, 16, ACCENT, True)
        actual = [m for m in matches if m["stage"] == stage]
        count = len(initial) // (2 ** column)
        current = []
        for row in range(count):
            center = 270 + 112 * (2 ** column * row + (2 ** column - 1) / 2)
            match = actual[row] if row < len(actual) else None
            if column == 0:
                participants = [match["left"], match["right"]]
                labels = [match.get("left_label", name(participants[0])), match.get("right_label", name(participants[1]))]
            else:
                feeds = previous[row * 2:row * 2 + 2]
                participants = [node["winner"] for node in feeds]
                labels = [name(node["winner"]) if node["winner"] else "To be decided"
                          for node in feeds]
            visible = match and not match.get("placeholder") and (stage_finished_by_day(stage) or match.get("bye"))
            winner = match["winner"] if visible else None
            scores = engine.series_score(match) if visible and not match.get("bye") else [None, None]
            node = {"x": x, "center": center, "labels": labels, "participants": participants,
                    "winner": winner, "scores": scores, "estimated_start": estimates[stage, row]}
            if column:
                for feeder in previous[row * 2:row * 2 + 2]:
                    end_x = feeder["x"] + 304
                    mid_x = x - 23
                    draw.line([(end_x, feeder["center"]), (mid_x, feeder["center"]),
                               (mid_x, center), (x, center)], fill="#42556c", width=2)
            current.append(node)
            nodes.append(node)
        previous = current
    for node in nodes:
        x, center = node["x"], node["center"]
        draw.rounded_rectangle((x, center - 40, x + 304, center + 40), radius=9, fill=PANEL,
                               outline=ACCENT if node["winner"] else "#35465b", width=2)
        draw.line((x + 10, center, x + 294, center), fill="#35465b")
        for i in range(2):
            y = center - 32 + i * 40
            won = node["winner"] and node["participants"][i] == node["winner"]
            text(x + 12, y, node["labels"][i], 18, ACCENT if won else TEXT,
                 bool(won), max_width=245)
            score = node["scores"][i]
            text(x + 272, y, str(score) if score is not None else "—", 18, GOLD if won else MUTED, True)
        start = node["estimated_start"]
        timing = (f"Est. {start.astimezone(zone):%a · %I:%M %p %Z}" if start
                  else "Bye · advances automatically")
        text(x + 8, center + 44, timing, 13, MUTED, max_width=290)
    group_y = 160 + bracket_height
    if event["groups"]:
        group_matches = [match for match in event["matches"] if match["stage"] == "groups"]
        group_label = "GROUP RESULTS" if all(match["winner"] for match in group_matches) else "GROUP STANDINGS"
        text(40, group_y, f"{group_label}  /  TOP TWO ADVANCE", 16, ACCENT, True)
        group_width = (width - 80) // 4
        for index, group in enumerate(event["groups"]):
            x = 40 + index * group_width
            text(x, group_y + 36, f"GROUP {group}", 16, TEXT, True)
            for rank, row in enumerate(engine.standings(event, group), 1):
                text(x, group_y + 68 + (rank - 1) * 32,
                     f"{rank}. {name(row['uid'])}  {row['wins']}–{row['losses']}", 15,
                     ACCENT if rank <= 2 else MUTED, rank <= 2, max_width=group_width - 16)
    champion = previous[0]["winner"] if previous else None
    if champion:
        text(40, height - 50, f"CHAMPION: {name(champion)}", 19, GOLD, True)
    output = io.BytesIO()
    canvas.save(output, format="PNG")
    output.seek(0)
    return output
