"""Tournament broadcast artwork using the same card assets as ranked matches."""

import copy
import io

import discord
from PIL import Image, ImageDraw


def render_match(cards, event, match, game_index, stage_label):
    game = match["games"][game_index]
    games = match["games"][:game_index + 1]
    scores = [sum(g["winner"] == match[side] for g in games) for side in ("left", "right")]
    blue, red = (87, 174, 255), (255, 111, 136)
    mint, white, muted = (96, 239, 185), (239, 245, 255), (142, 160, 185)
    canvas = Image.new("RGB", (1040, 932))
    draw = ImageDraw.Draw(canvas)
    for y in range(canvas.height):
        t = y / canvas.height
        draw.line((0, y, 1039, y), fill=(int(14 - 5*t), int(25 - 10*t), int(42 - 17*t)))

    def label(x, y, value, size, color=white, bold=False, anchor="la", max_width=None):
        font = cards.get_card_font(size, bold=bold)
        if max_width:
            value = cards.fit_line(draw, value, max_width, font=font)
        draw.text((x, y), value, font=font, fill=color, anchor=anchor)

    group = f" / GROUP {match['group']}" if match.get("group") else ""
    label(38, 29, f"{stage_label.upper()}{group} / GAME {game_index + 1}", 18, muted, True,
          max_width=760)
    label(1002, 29, "BEST OF FIVE", 15, muted, True, "ra")
    draw.line((38, 68, 1002, 68), fill=(46, 63, 83), width=1)
    left, right = (event["entrants"][match[side]] for side in ("left", "right"))
    seeds = event["tournament_seeds"]
    label(45, 88, f"#{seeds[match['left']]}", 22, blue, True)
    label(95, 80, left["name"], 35, white, True, max_width=315)
    label(995, 88, f"#{seeds[match['right']]}", 22, red, True, "ra")
    label(941, 80, right["name"], 35, white, True, "ra", max_width=315)
    label(520, 69, f"{scores[0]} : {scores[1]}", 61, white, True, "ma")

    for side, x in (("left", 47), ("right", 883)):
        draw.line((x, 141, x+15, 156), fill=muted, width=2)
        draw.line((x+15, 141, x, 156), fill=muted, width=2)
        draw.line((x-2, 152, x+4, 158), fill=(234, 195, 107), width=2)
        draw.line((x+11, 158, x+17, 152), fill=(234, 195, 107), width=2)
        entrant = event["entrants"][match[side]]
        label(x+27, 134, str(game.get(f"{side}_power", entrant["power"])), 22, muted, True)
    for side_index, start, color in ((0, 47, blue), (1, 902, red)):
        for i in range(3):
            x = start + i*34
            draw.rounded_rectangle((x, 183, x+24, 191), radius=4,
                                   fill=color if i < scores[side_index] else (42, 54, 73))

    for side, y, color in (("left", 223, blue), ("right", 575, red)):
        won = game["winner"] == match[side]
        draw.rounded_rectangle((29, y, 1011, y+327), radius=16, fill=(16, 25, 39),
                               outline=mint if won else (56, 63, 83), width=2 if won else 1)
        draw.rounded_rectangle((39, y+17, 43, y+46), radius=2, fill=color)
        label(55, y+18, "BLUE SIDE" if side == "left" else "RED SIDE", 13, color, True)
        if won:
            draw.rounded_rectangle((840, y+12, 993, y+40), radius=9, fill=(22, 67, 56))
            label(916, y+15, "GAME WINNER", 13, mint, True, "ma")
        slots = copy.deepcopy(event["entrants"][match[side]]["slots"])
        if f"{side}_gold_rolls" in game:
            cards.apply_ranked_gold_to_image_slots(slots, game[f"{side}_gold_rolls"])
        file = cards.make_team_lineup_file(slots, f"tournament_{side}", event["game"])
        if not file:
            raise ValueError("Tournament lineup artwork unavailable")
        try:
            with Image.open(file.fp) as row:
                for column in range(5):
                    card = row.crop((12+column*166, 12, 168+column*166, 250)).convert("RGB")
                    card = card.resize((181, 276), Image.Resampling.LANCZOS)
                    canvas.paste(card, (49+column*190, y+46))
        finally:
            file.close()

    output = io.BytesIO()
    canvas.save(output, format="PNG")
    output.seek(0)
    return discord.File(output, filename=f"tournament-{event['week']}-{match['id']}-game-{game_index+1}.png")
