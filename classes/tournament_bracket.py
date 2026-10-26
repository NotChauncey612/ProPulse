"""Render public tournament bracket pictures from saved results using Pillow."""

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import ImageFont

from . import tournament_engine as engine


ROUNDS = [("preliminary", "PRELIMINARIES"), ("quarterfinal", "QUARTERFINALS"),
          ("semifinal", "SEMIFINALS"), ("final", "FINAL")]


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
    """Show group standings through Thursday, then a separate knockout bracket."""
    from .tournament_layout import render_groups, render_knockout, show_groups

    if show_groups(event, report_day):
        return render_groups(event, report_day)
    return render_knockout(event, report_day, title)
