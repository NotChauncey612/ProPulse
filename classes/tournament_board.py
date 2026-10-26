"""Shared broadcast styling for tournament standings and knockout boards."""

import io

from PIL import Image, ImageDraw


TEXT = "#eef4ff"
MUTED = "#8b9fb8"
BLUE = "#57aeff"
MINT = "#60efb9"
GOLD = "#efcb79"
PANEL = "#111e2e"
LINE = "#293d53"


class Board:
    def __init__(self, width, height, font):
        self.width = width
        self.font = font
        self.image = Image.new("RGB", (width, height))
        self.draw = ImageDraw.Draw(self.image)
        for y in range(height):
            t = y / height
            self.draw.line((0, y, width, y), fill=(int(15-5*t), int(27-10*t), int(44-17*t)))

    def text(self, x, y, value, size=18, color=TEXT, bold=False, anchor="la", width=None):
        face = self.font(size, bold)
        value = str(value).replace("\n", " ").replace("\r", " ")
        if width:
            original = value
            while value and self.draw.textlength(value, font=face) > width:
                original = original[:-1]
                value = original + "…" if original else ""
        self.draw.text((x, y), value, font=face, fill=color, anchor=anchor)

    def pill(self, box, value):
        self.draw.rounded_rectangle(box, radius=10, fill="#193c36")
        self.text((box[0]+box[2])/2, box[1]+7, value, 13, MINT, True, "ma", box[2]-box[0]-16)

    def header(self, eyebrow, title, subtitle, status):
        self.text(48, 29, eyebrow, 16, BLUE, True, width=self.width-96)
        self.text(48, 60, title, 42, TEXT, True, width=self.width-350)
        self.text(48, 119, subtitle, 17, MUTED, width=self.width-96)
        self.pill((self.width-262, 59, self.width-48, 95), status)
        self.draw.line((48, 163, self.width-48, 163), fill=LINE, width=1)

    def png(self):
        output = io.BytesIO()
        self.image.save(output, format="PNG")
        output.seek(0)
        return output
