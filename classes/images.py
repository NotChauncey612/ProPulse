import asyncio
import io
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
import requests
from discord.ext import commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

from .storage import load_json, save_json


CARDS_PATH = "data/cards.json"
SUBMISSIONS_PATH = "data/image_submissions.json"
TEAM_LOGOS_PATH = Path("assets/cards/team_logos")
ROLE_ICONS_PATH = Path("assets/cards/role_icons")
REWARD_CASH = 50
CASH_EMOJI = "💵"
REVIEW_CHANNEL_ID = os.getenv("IMAGE_REVIEW_CHANNEL_ID")
VOTING_CHANNEL_ID = os.getenv("IMAGE_VOTING_CHANNEL_ID")
HIDDEN_SUBMISSION_STATUSES = {"deleted"}
COUNTED_SUBMISSION_STATUSES = {"pending", "approved"}
PREVIEW_CARD_WIDTH = 312
PREVIEW_CARD_HEIGHT = 476
PREVIEW_PADDING = 14
PREVIEW_SIZE = (
    PREVIEW_CARD_WIDTH + PREVIEW_PADDING * 2,
    PREVIEW_CARD_HEIGHT + PREVIEW_PADDING * 2,
)
PREVIEW_SILVER = (192, 198, 207)

PACK_LOL_MSI = "lol_msi_2026"
PACK_VALORANT_LONDON = "valorant_masters_london_2026"
VALORANT_GAME = "Valorant"
VALORANT_SET = "VCT 26"
VALORANT_EVENT_NAME = "Valorant Masters London 2026"

VALORANT_MASTERS_TEAMS = [
    "G2 Esports",
    "Leviatan Esports",
    "NRG",
    "Team Heretics",
    "Team Vitality",
    "FUT Esports",
    "Paper Rex",
    "FULL SENSE",
    "GLOBAL ESPORTS",
    "EDWARD Gaming",
    "Xi Lai Gaming",
    "DRAGON RANGER GAMING",
]

VALORANT_ROLE_LABELS = {
    "INITIATOR": "Initiator",
    "DUELIST": "Duelist",
    "CONTROLLER": "Controller",
    "SENTINEL": "Sentinel",
    "FLEX": "Flex",
}


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def normalize_key(value):
    return " ".join(str(value).casefold().split())


def submission_safe_name(value):
    return "".join(char.lower() if char.isalnum() else "-" for char in str(value)).strip("-") or "player"


def preview_font(size=12, bold=False):
    font_names = ["arialbd.ttf" if bold else "arial.ttf", "segoeuib.ttf" if bold else "segoeui.ttf"]
    for font_name in font_names:
        try:
            return ImageFont.truetype(font_name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def fit_preview_line(draw, text, max_width, font=None):
    original = str(text)
    if draw.textbbox((0, 0), original, font=font)[2] <= max_width:
        return original

    text = original
    while text and draw.textbbox((0, 0), f"{text}...", font=font)[2] > max_width:
        text = text[:-1]
    return f"{text}..." if text else ""


def draw_preview_centered_text(draw, box, text, fill, font=None):
    left, top, right, bottom = box
    text_box = draw.textbbox((0, 0), text, font=font)
    text_width = text_box[2] - text_box[0]
    text_height = text_box[3] - text_box[1]
    x = left + ((right - left) - text_width) // 2
    y = top + ((bottom - top) - text_height) // 2
    draw.text((x, y), text, fill=fill, font=font)


def asset_slug(value):
    value = str(value).lower()
    value = "".join(ch if ch.isalnum() else "-" for ch in value)
    return "-".join(part for part in value.split("-") if part)


def find_asset(path, slug):
    if not slug:
        return None
    for extension in (".png", ".jpg", ".jpeg", ".webp"):
        candidate = path / f"{slug}{extension}"
        if candidate.exists():
            return candidate
    return None


def paste_contained_image(canvas, image, box):
    left, top, right, bottom = box
    max_size = (max(1, right - left), max(1, bottom - top))
    fitted = ImageOps.contain(image.convert("RGBA"), max_size)
    x = left + (max_size[0] - fitted.width) // 2
    y = top + (max_size[1] - fitted.height) // 2
    canvas.paste(fitted, (x, y), fitted)


def draw_badge_asset(canvas, draw, path, box, outline=None):
    left, top, right, bottom = box
    draw.rounded_rectangle(box, radius=7, fill=(9, 13, 20), outline=outline or (58, 65, 78), width=1)
    if path:
        with Image.open(path) as badge:
            paste_contained_image(canvas, badge, (left + 5, top + 5, right - 5, bottom - 5))


class Images(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    async def moderator_check(self, ctx):
        users_cog = self.bot.get_cog("Users")
        if users_cog and hasattr(users_cog, "moderator_check"):
            return await users_cog.moderator_check(ctx)

        guild_permissions = getattr(ctx.author, "guild_permissions", None)
        if getattr(guild_permissions, "administrator", False):
            return True

        raise commands.MissingPermissions(["bot_moderator"])

    def load_cards(self):
        data = load_json(CARDS_PATH, default={})
        return data if isinstance(data, dict) else {}

    def load_submissions(self):
        data = load_json(SUBMISSIONS_PATH, default={"submissions": []})
        if isinstance(data, dict) and isinstance(data.get("submissions"), list):
            return data
        if isinstance(data, list):
            return {"submissions": data}
        return {"submissions": []}

    def save_submissions(self, data):
        save_json(SUBMISSIONS_PATH, data)

    def find_submission(self, submission_id):
        data = self.load_submissions()
        for submission in data.get("submissions", []):
            if str(submission.get("id")) == str(submission_id):
                return data, submission
        return data, None

    def valorant_team_cards(self):
        cards_data = self.load_cards()
        teams = {}
        leagues = (
            cards_data
            .get("games", {})
            .get(VALORANT_GAME, {})
            .get("sets", {})
            .get(VALORANT_SET, {})
            .get("leagues", {})
        )

        for league_name, league_data in leagues.items():
            for team_name, team_data in league_data.get("teams", {}).items():
                if team_name not in VALORANT_MASTERS_TEAMS:
                    continue
                teams[team_name] = {
                    "league": league_name,
                    "cards": team_data.get("cards", []),
                }

        return teams

    def valorant_player_card(self, team_name, player_name):
        team_info = self.valorant_team_cards().get(team_name, {})
        for card in team_info.get("cards", []):
            if normalize_key(card.get("ign")) == normalize_key(player_name):
                return card
        return {}

    def submission_count(self, event_name, team_name, player_name):
        submissions = self.load_submissions().get("submissions", [])
        event_key = normalize_key(event_name)
        team_key = normalize_key(team_name)
        player_key = normalize_key(player_name)

        return sum(
            1
            for submission in submissions
            if normalize_key(submission.get("event")) == event_key
            and normalize_key(submission.get("team")) == team_key
            and normalize_key(submission.get("player")) == player_key
            and submission.get("status", "pending") in COUNTED_SUBMISSION_STATUSES
        )

    def build_packs_embed(self):
        embed = discord.Embed(
            title="Image Review Packs",
            description=(
                f"Approved player images reward the submitter with {CASH_EMOJI} {REWARD_CASH} cash.\n\n"
                "Choose a pack below to see what images are needed."
            ),
            color=discord.Color.dark_grey(),
        )
        embed.add_field(name="LOL MSI 2026", value="Coming soon.", inline=False)
        embed.add_field(name=VALORANT_EVENT_NAME, value="Open for image submissions.", inline=False)
        return embed

    def build_valorant_teams_embed(self):
        teams = self.valorant_team_cards()
        lines = []
        for team_name in VALORANT_MASTERS_TEAMS:
            team_info = teams.get(team_name, {})
            cards = team_info.get("cards", [])
            total = sum(self.submission_count(VALORANT_EVENT_NAME, team_name, card.get("ign", "")) for card in cards)
            lines.append(f"**{team_name}** - {total} submitted")

        embed = discord.Embed(
            title=VALORANT_EVENT_NAME,
            description=(
                f"Each approved image rewards the user with {CASH_EMOJI} {REWARD_CASH} cash.\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Choose a team, then choose the player you want to submit an image for.")
        return embed

    def build_players_embed(self, team_name):
        teams = self.valorant_team_cards()
        team_info = teams.get(team_name)
        if not team_info:
            return discord.Embed(
                title=team_name,
                description="No card data was found for this team yet.",
                color=discord.Color.red(),
            )

        lines = []
        for card in team_info.get("cards", []):
            player = card.get("ign", "Unknown")
            role = card.get("role", "Unknown")
            count = self.submission_count(VALORANT_EVENT_NAME, team_name, player)
            lines.append(f"**{player}** ({role}) - {count} submitted")

        embed = discord.Embed(
            title=f"{team_name} Images",
            description=(
                f"Each approved image rewards the user with {CASH_EMOJI} {REWARD_CASH} cash.\n\n"
                + "\n".join(lines)
            ),
            color=discord.Color.red(),
        )
        embed.set_footer(text="Choose a player to start an image submission.")
        return embed

    def build_submit_embed(self, team_name, player_name):
        count = self.submission_count(VALORANT_EVENT_NAME, team_name, player_name)
        embed = discord.Embed(
            title=f"Submit Image: {player_name}",
            description=(
                f"Each approved image rewards the user with {CASH_EMOJI} {REWARD_CASH} cash.\n\n"
                f"Current submissions for **{player_name}**: **{count}**"
            ),
            color=discord.Color.red(),
        )
        embed.add_field(name="Team", value=team_name, inline=True)
        embed.add_field(name="Event", value=VALORANT_EVENT_NAME, inline=True)
        return embed

    def build_image_preview_file(self, attachment, team_name, player_name):
        return self.build_image_preview_file_from_url(attachment.url, team_name, player_name)

    def build_image_preview_file_from_url(self, image_url, team_name, player_name):
        headers = {"User-Agent": "ProPulse image preview"}
        response = requests.get(image_url, headers=headers, timeout=20)
        response.raise_for_status()

        with Image.open(io.BytesIO(response.content)) as source:
            canvas = Image.new("RGB", PREVIEW_SIZE, (15, 19, 29))
            draw = ImageDraw.Draw(canvas)
            card = self.valorant_player_card(team_name, player_name)
            role = str(card.get("role", "Unknown")).upper()
            role_label = VALORANT_ROLE_LABELS.get(role, role.title())
            player_label = fit_preview_line(draw, player_name, PREVIEW_CARD_WIDTH - 48, preview_font(28, bold=True))
            set_label = fit_preview_line(draw, f"{role_label} - {VALORANT_EVENT_NAME}", PREVIEW_CARD_WIDTH - 48, preview_font(12))

            x = PREVIEW_PADDING
            y = PREVIEW_PADDING
            draw.rounded_rectangle(
                (x, y, x + PREVIEW_CARD_WIDTH, y + PREVIEW_CARD_HEIGHT),
                radius=12,
                fill=(20, 25, 36),
                outline=PREVIEW_SILVER,
                width=5,
            )

            image_box = (x + 10, y + 10, x + PREVIEW_CARD_WIDTH - 10, y + PREVIEW_CARD_HEIGHT - 10)
            image_size = (image_box[2] - image_box[0], image_box[3] - image_box[1])
            fitted = ImageOps.fit(source.convert("RGB"), image_size)
            canvas.paste(fitted, image_box[:2])

            top_shadow = Image.new("RGBA", (PREVIEW_CARD_WIDTH - 20, 108), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(top_shadow)
            for row in range(top_shadow.height):
                alpha = int(165 * (1 - row / top_shadow.height))
                shadow_draw.line((0, row, top_shadow.width, row), fill=(0, 0, 0, alpha))
            canvas.paste(top_shadow, (x + 10, y + 10), top_shadow)

            bottom_height = 116
            bottom_shadow = Image.new("RGBA", (PREVIEW_CARD_WIDTH - 20, bottom_height), (0, 0, 0, 0))
            shadow_draw = ImageDraw.Draw(bottom_shadow)
            for row in range(bottom_height):
                alpha = int(230 * (row / max(1, bottom_height - 1)))
                shadow_draw.line((0, row, bottom_shadow.width, row), fill=(0, 0, 0, alpha))
            canvas.paste(bottom_shadow, (x + 10, y + PREVIEW_CARD_HEIGHT - 10 - bottom_height), bottom_shadow)

            team_logo = find_asset(TEAM_LOGOS_PATH, asset_slug(team_name))
            draw_badge_asset(canvas, draw, team_logo, (x + 24, y + 24, x + 88, y + 88), outline=PREVIEW_SILVER)
            role_icon = find_asset(ROLE_ICONS_PATH, asset_slug(role))
            draw_badge_asset(canvas, draw, role_icon, (x + PREVIEW_CARD_WIDTH - 88, y + 24, x + PREVIEW_CARD_WIDTH - 24, y + 88), outline=PREVIEW_SILVER)

            draw_preview_centered_text(
                draw,
                (x + 24, y + PREVIEW_CARD_HEIGHT - 104, x + PREVIEW_CARD_WIDTH - 24, y + PREVIEW_CARD_HEIGHT - 70),
                player_label,
                (248, 250, 253),
                font=preview_font(30, bold=True),
            )
            draw_preview_centered_text(
                draw,
                (x + 24, y + PREVIEW_CARD_HEIGHT - 68, x + PREVIEW_CARD_WIDTH - 24, y + PREVIEW_CARD_HEIGHT - 42),
                set_label,
                (164, 173, 188),
                font=preview_font(12),
            )

            buffer = io.BytesIO()
            canvas.save(buffer, format="PNG")
            buffer.seek(0)
            return discord.File(buffer, filename=f"image-preview-{submission_safe_name(player_name)}.png")

    def build_submission_embed(self, submission):
        status = submission.get("status", "pending").title()
        color = discord.Color.gold()
        if submission.get("status") == "approved":
            color = discord.Color.green()
        elif submission.get("status") in {"rejected", "deleted"}:
            color = discord.Color.red()

        embed = discord.Embed(
            title=f"Image Submission #{submission.get('id')}",
            description=f"Status: **{status}**\nReward on approval: **{CASH_EMOJI} {submission.get('reward_cash', REWARD_CASH)} cash**",
            color=color,
        )
        embed.add_field(name="Event", value=submission.get("event", "Unknown"), inline=False)
        embed.add_field(name="Team", value=submission.get("team", "Unknown"), inline=True)
        embed.add_field(name="Player", value=submission.get("player", "Unknown"), inline=True)
        embed.add_field(
            name="Submitter",
            value=f"{submission.get('username', 'Unknown')} (`{submission.get('user_id', 'Unknown')}`)",
            inline=False,
        )
        if submission.get("attachment_url"):
            embed.set_image(url=submission["attachment_url"])
        embed.set_footer(text=f"Submitted: {submission.get('submitted_at', 'Unknown')}")
        return embed

    def filtered_submissions(self, status=None):
        submissions = list(self.load_submissions().get("submissions", []))
        if status and status != "all":
            submissions = [
                submission
                for submission in submissions
                if submission.get("status", "pending").casefold() == status.casefold()
            ]
        return sorted(submissions, key=lambda item: int(item.get("id", 0)), reverse=True)

    def player_submissions(self, event_name, team_name, player_name):
        submissions = self.load_submissions().get("submissions", [])
        event_key = normalize_key(event_name)
        team_key = normalize_key(team_name)
        player_key = normalize_key(player_name)
        results = [
            submission
            for submission in submissions
            if normalize_key(submission.get("event")) == event_key
            and normalize_key(submission.get("team")) == team_key
            and normalize_key(submission.get("player")) == player_key
            and submission.get("status", "pending") not in HIDDEN_SUBMISSION_STATUSES
        ]
        return sorted(results, key=lambda item: int(item.get("id", 0)), reverse=True)

    def delete_own_submission(self, submission_id, user_id):
        data, submission = self.find_submission(submission_id)
        if not submission:
            return False, "That submission could not be found."
        if str(submission.get("user_id")) != str(user_id):
            return False, "You can only delete your own submissions."
        if submission.get("status", "pending") == "approved":
            return False, "Approved submissions cannot be deleted."
        if submission.get("status", "pending") == "deleted":
            return False, "That submission is already deleted."

        submission["status"] = "deleted"
        submission["deleted_at"] = utc_now_iso()
        self.save_submissions(data)
        return True, f"Deleted submission #{submission_id}."

    def record_submission(self, user, team_name, player_name, attachment):
        data = self.load_submissions()
        submission = {
            "id": len(data["submissions"]) + 1,
            "event": VALORANT_EVENT_NAME,
            "team": team_name,
            "player": player_name,
            "user_id": str(user.id),
            "username": str(user),
            "attachment_url": attachment.url,
            "filename": attachment.filename,
            "status": "pending",
            "reward_cash": REWARD_CASH,
            "submitted_at": utc_now_iso(),
            "reviewed_at": None,
            "reviewer_id": None,
        }
        data["submissions"].append(submission)
        self.save_submissions(data)
        return submission

    async def send_review_copy(self, submitter, submission, attachment):
        if not REVIEW_CHANNEL_ID or not REVIEW_CHANNEL_ID.isdigit():
            return

        channel = self.bot.get_channel(int(REVIEW_CHANNEL_ID))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(REVIEW_CHANNEL_ID))
            except discord.DiscordException:
                return

        embed = discord.Embed(
            title="Pending Image Review",
            description=(
                f"Submission #{submission['id']}\n"
                f"Reward on approval: {CASH_EMOJI} {REWARD_CASH} cash"
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Submitter", value=f"{submitter.mention} (`{submitter.id}`)", inline=False)
        embed.add_field(name="Event", value=submission["event"], inline=False)
        embed.add_field(name="Team", value=submission["team"], inline=True)
        embed.add_field(name="Player", value=submission["player"], inline=True)
        embed.set_image(url=attachment.url)
        embed.set_footer(text=f"Use .approveimage {submission['id']} or .rejectimage {submission['id']}.")
        await channel.send(embed=embed)

    async def send_voting_copy(self, submission, attachment):
        if not VOTING_CHANNEL_ID or not VOTING_CHANNEL_ID.isdigit():
            return

        channel = self.bot.get_channel(int(VOTING_CHANNEL_ID))
        if channel is None:
            try:
                channel = await self.bot.fetch_channel(int(VOTING_CHANNEL_ID))
            except discord.DiscordException:
                return

        embed = self.build_submission_embed(submission)
        embed.title = f"Vote: {embed.title}"
        embed.description += "\n\nReact with thumbs up or thumbs down to help review this image."
        message = await channel.send(embed=embed)
        for emoji in ("👍", "👎"):
            try:
                await message.add_reaction(emoji)
            except discord.DiscordException:
                pass

    @commands.command(name="images")
    async def images_command(self, ctx):
        view = ImagePackView(self, ctx.author.id)
        await ctx.send(embed=self.build_packs_embed(), view=view)

    @commands.command(name="approveimage", aliases=["approveimg"])
    @commands.check(lambda ctx: ctx.cog.moderator_check(ctx))
    async def approve_image_command(self, ctx, submission_id: int):
        data, submission = self.find_submission(submission_id)
        if not submission:
            await ctx.send(f"No image submission found with ID {submission_id}.")
            return

        if submission.get("status") == "approved":
            await ctx.send(f"Submission #{submission_id} is already approved.")
            return

        submission["status"] = "approved"
        submission["reviewed_at"] = utc_now_iso()
        submission["reviewer_id"] = str(ctx.author.id)
        self.save_submissions(data)

        awarded = False
        users_cog = self.bot.get_cog("Users")
        if users_cog:
            user_id = str(submission.get("user_id"))
            user = ctx.guild.get_member(int(user_id)) if ctx.guild and user_id.isdigit() else None
            if user is None and user_id.isdigit():
                try:
                    user = await self.bot.fetch_user(int(user_id))
                except discord.DiscordException:
                    user = None

            if user is not None:
                profile = users_cog.get_profile(user)
                profile["cash"] = profile.get("cash", 0) + submission.get("reward_cash", REWARD_CASH)
                users_cog.save_users()
                awarded = True

        reward_text = f"and awarded {CASH_EMOJI} {submission.get('reward_cash', REWARD_CASH)} cash"
        if not awarded:
            reward_text = "but could not award cash because the submitter profile was not found"

        await ctx.send(f"Approved submission #{submission_id} for {submission.get('player')} {reward_text}.")

    @commands.command(name="rejectimage", aliases=["rejectimg"])
    @commands.check(lambda ctx: ctx.cog.moderator_check(ctx))
    async def reject_image_command(self, ctx, submission_id: int):
        data, submission = self.find_submission(submission_id)
        if not submission:
            await ctx.send(f"No image submission found with ID {submission_id}.")
            return

        submission["status"] = "rejected"
        submission["reviewed_at"] = utc_now_iso()
        submission["reviewer_id"] = str(ctx.author.id)
        self.save_submissions(data)
        await ctx.send(f"Rejected submission #{submission_id} for {submission.get('player')}.")


class AuthorOnlyView(discord.ui.View):
    def __init__(self, cog, author_id, *, timeout=180):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.author_id = author_id

    async def interaction_check(self, interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("This image menu belongs to someone else. Run `.images` to start your own.", ephemeral=True)
            return False
        return True


class ImagePackSelect(discord.ui.Select):
    def __init__(self, image_view):
        self.image_view = image_view
        options = [
            discord.SelectOption(label="LOL MSI 2026", value=PACK_LOL_MSI, description="Coming soon"),
            discord.SelectOption(label=VALORANT_EVENT_NAME, value=PACK_VALORANT_LONDON, description="Submit Valorant player images"),
        ]
        super().__init__(placeholder="Choose an image pack", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        selected = self.values[0]
        if selected == PACK_LOL_MSI:
            embed = discord.Embed(
                title="LOL MSI 2026",
                description=f"Each approved image will reward the user with {CASH_EMOJI} {REWARD_CASH} cash.\n\nThis pack is coming soon.",
                color=discord.Color.blue(),
            )
            await interaction.response.edit_message(embed=embed, view=ImagePackView(self.image_view.cog, self.image_view.author_id))
            return

        view = ValorantTeamView(self.image_view.cog, self.image_view.author_id)
        await interaction.response.edit_message(embed=self.image_view.cog.build_valorant_teams_embed(), view=view)


class ImagePackView(AuthorOnlyView):
    def __init__(self, cog, author_id):
        super().__init__(cog, author_id)
        self.add_item(ImagePackSelect(self))


class ValorantTeamSelect(discord.ui.Select):
    def __init__(self, image_view):
        self.image_view = image_view
        teams = image_view.cog.valorant_team_cards()
        options = []
        for team_name in VALORANT_MASTERS_TEAMS:
            team_info = teams.get(team_name, {})
            league = team_info.get("league", "Valorant")
            options.append(discord.SelectOption(label=team_name, value=team_name, description=league))
        super().__init__(placeholder="Choose a Valorant team", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        team_name = self.values[0]
        view = ValorantPlayerView(self.image_view.cog, self.image_view.author_id, team_name)
        await interaction.response.edit_message(embed=self.image_view.cog.build_players_embed(team_name), view=view)


class ValorantTeamView(AuthorOnlyView):
    def __init__(self, cog, author_id):
        super().__init__(cog, author_id)
        self.add_item(ValorantTeamSelect(self))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction, button):
        view = ImagePackView(self.cog, self.author_id)
        await interaction.response.edit_message(embed=self.cog.build_packs_embed(), view=view)


class ValorantPlayerSelect(discord.ui.Select):
    def __init__(self, image_view):
        self.image_view = image_view
        team_info = image_view.cog.valorant_team_cards().get(image_view.team_name, {})
        options = []
        for card in team_info.get("cards", []):
            player = card.get("ign", "Unknown")
            role = card.get("role", "Unknown")
            count = image_view.cog.submission_count(VALORANT_EVENT_NAME, image_view.team_name, player)
            options.append(
                discord.SelectOption(
                    label=player[:100],
                    value=player[:100],
                    description=f"{role} - {count} submitted"[:100],
                )
            )
        if not options:
            options.append(discord.SelectOption(label="No players found", value="none", description="No card data available"))
        super().__init__(placeholder="Choose a player", min_values=1, max_values=1, options=options)

    async def callback(self, interaction):
        player_name = self.values[0]
        if player_name == "none":
            await interaction.response.send_message("No players are available for that team yet.", ephemeral=True)
            return

        view = SubmitImageView(self.image_view.cog, self.image_view.author_id, self.image_view.team_name, player_name)
        await interaction.response.edit_message(
            embed=self.image_view.cog.build_submit_embed(self.image_view.team_name, player_name),
            view=view,
        )


class ValorantPlayerView(AuthorOnlyView):
    def __init__(self, cog, author_id, team_name):
        super().__init__(cog, author_id)
        self.team_name = team_name
        self.add_item(ValorantPlayerSelect(self))

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction, button):
        view = ValorantTeamView(self.cog, self.author_id)
        await interaction.response.edit_message(embed=self.cog.build_valorant_teams_embed(), view=view)


class SubmitImageView(AuthorOnlyView):
    def __init__(self, cog, author_id, team_name, player_name):
        super().__init__(cog, author_id, timeout=300)
        self.team_name = team_name
        self.player_name = player_name

    @discord.ui.button(label="Submit Image", style=discord.ButtonStyle.success, row=0)
    async def submit_button(self, interaction, button):
        await interaction.response.send_message("Send one image attachment in this channel within 2 minutes.", ephemeral=True)

        def check(message):
            if message.author.id != self.author_id:
                return False
            if message.channel.id != interaction.channel_id:
                return False
            return bool(message.attachments)

        try:
            message = await self.cog.bot.wait_for("message", check=check, timeout=120)
        except asyncio.TimeoutError:
            await interaction.followup.send("No image was received, so the submission was cancelled.", ephemeral=True)
            return

        attachment = message.attachments[0]
        if not (attachment.content_type or "").startswith("image/"):
            await interaction.followup.send("That attachment does not look like an image. Please start again with `.images`.", ephemeral=True)
            return

        embed = discord.Embed(
            title="Confirm Image Submission",
            description=(
                "Review the card preview below.\n"
                f"If this image is approved, you will receive {CASH_EMOJI} {REWARD_CASH} cash."
            ),
            color=discord.Color.gold(),
        )
        embed.add_field(name="Team", value=self.team_name, inline=True)
        embed.add_field(name="Player", value=self.player_name, inline=True)

        try:
            preview_file = self.cog.build_image_preview_file(attachment, self.team_name, self.player_name)
            embed.set_image(url=f"attachment://{preview_file.filename}")
            view = ConfirmImageSubmissionView(self.cog, self.author_id, self.team_name, self.player_name, attachment)
            await interaction.followup.send(embed=embed, file=preview_file, view=view)
        except Exception:
            await interaction.followup.send("I could not generate a preview for that image. Please try another image.", ephemeral=True)

    @discord.ui.button(label="View Submissions", style=discord.ButtonStyle.primary, row=0)
    async def view_submissions_button(self, interaction, button):
        view = PlayerSubmissionView(self.cog, self.author_id, self.team_name, self.player_name)
        embed, file = view.build_message()
        attachments = [file] if file else []
        await interaction.response.edit_message(embed=embed, attachments=attachments, view=view)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=1)
    async def back_button(self, interaction, button):
        view = ValorantPlayerView(self.cog, self.author_id, self.team_name)
        await interaction.response.edit_message(embed=self.cog.build_players_embed(self.team_name), view=view)


class ConfirmImageSubmissionView(AuthorOnlyView):
    def __init__(self, cog, author_id, team_name, player_name, attachment):
        super().__init__(cog, author_id, timeout=300)
        self.team_name = team_name
        self.player_name = player_name
        self.attachment = attachment

    @discord.ui.button(label="Confirm Submission", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction, button):
        submission = self.cog.record_submission(interaction.user, self.team_name, self.player_name, self.attachment)
        await self.cog.send_review_copy(interaction.user, submission, self.attachment)
        await self.cog.send_voting_copy(submission, self.attachment)

        embed = discord.Embed(
            title="Image Submitted",
            description=(
                f"Submission #{submission['id']} is pending review.\n"
                f"If approved, you will receive {CASH_EMOJI} {REWARD_CASH} cash."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="Team", value=self.team_name, inline=True)
        embed.add_field(name="Player", value=self.player_name, inline=True)
        if interaction.message and interaction.message.embeds:
            image_url = interaction.message.embeds[0].image.url
            if image_url:
                embed.set_image(url=image_url)

        await interaction.response.edit_message(embed=embed, view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction, button):
        embed = discord.Embed(
            title="Image Submission Cancelled",
            description="No image was submitted.",
            color=discord.Color.red(),
        )
        await interaction.response.edit_message(embed=embed, attachments=[], view=None)


class DeleteOwnSubmissionSelect(discord.ui.Select):
    def __init__(self, image_view, submissions):
        self.image_view = image_view
        options = [
            discord.SelectOption(
                label=f"Delete #{submission.get('id')}",
                value=str(submission.get("id")),
                description=f"{submission.get('status', 'pending').title()} - {submission.get('filename', 'image')}"[:100],
            )
            for submission in submissions
        ]
        super().__init__(
            placeholder="Delete one of your submissions",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction):
        success, message = self.image_view.cog.delete_own_submission(self.values[0], interaction.user.id)
        view = PlayerSubmissionView(
            self.image_view.cog,
            self.image_view.author_id,
            self.image_view.team_name,
            self.image_view.player_name,
            page=self.image_view.page,
        )
        if view.page >= view.total_pages():
            view.page = max(0, view.total_pages() - 1)
            view.refresh_items()
        embed, file = view.build_message()
        if success:
            embed.description = f"{message}\n\n{embed.description or ''}".strip()
        else:
            embed.description = f"{message}\n\n{embed.description or ''}".strip()
        attachments = [file] if file else []
        await interaction.response.edit_message(embed=embed, attachments=attachments, view=view)


class PlayerSubmissionView(AuthorOnlyView):
    def __init__(self, cog, author_id, team_name, player_name, page=0):
        super().__init__(cog, author_id, timeout=300)
        self.team_name = team_name
        self.player_name = player_name
        self.page = page
        self.refresh_items()

    def submissions(self):
        return self.cog.player_submissions(VALORANT_EVENT_NAME, self.team_name, self.player_name)

    def total_pages(self):
        return max(1, len(self.submissions()))

    def page_items(self):
        submissions = self.submissions()
        if not submissions:
            return []
        return submissions[self.page:self.page + 1]

    def own_deletable_items(self):
        return [
            submission
            for submission in self.page_items()
            if str(submission.get("user_id")) == str(self.author_id)
            and submission.get("status", "pending") != "approved"
        ]

    def refresh_items(self):
        for item in list(self.children):
            if isinstance(item, DeleteOwnSubmissionSelect):
                self.remove_item(item)

        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages() - 1

        deletable = self.own_deletable_items()
        if deletable:
            self.add_item(DeleteOwnSubmissionSelect(self, deletable[:25]))

    def build_embed(self):
        count = self.cog.submission_count(VALORANT_EVENT_NAME, self.team_name, self.player_name)
        embed = discord.Embed(
            title=f"{self.player_name} Submissions",
            description=f"Each approved image rewards the user with {CASH_EMOJI} {REWARD_CASH} cash.\nCurrent submissions: **{count}**",
            color=discord.Color.red(),
        )

        submissions = self.page_items()
        if not submissions:
            embed.description += "\n\nNo submissions have been added for this player yet."
            return embed

        submission = submissions[0]
        owner_note = " - yours" if str(submission.get("user_id")) == str(self.author_id) else ""
        embed.add_field(
            name=f"Submission #{submission.get('id')}",
            value=(
                f"Status: **{submission.get('status', 'pending').title()}**{owner_note}\n"
                f"Submitted by {submission.get('username', 'Unknown')}"
            ),
            inline=False,
        )
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages()} - Use the delete dropdown for your removable submissions.")
        return embed

    def build_message(self):
        embed = self.build_embed()
        submissions = self.page_items()
        if not submissions:
            return embed, None

        submission = submissions[0]
        image_url = submission.get("attachment_url")
        if not image_url:
            return embed, None

        try:
            file = self.cog.build_image_preview_file_from_url(image_url, self.team_name, self.player_name)
        except Exception:
            embed.description += "\n\nI could not render this submission preview."
            return embed, None

        embed.set_image(url=f"attachment://{file.filename}")
        return embed, file

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def previous_button(self, interaction, button):
        self.page = max(0, self.page - 1)
        self.refresh_items()
        embed, file = self.build_message()
        attachments = [file] if file else []
        await interaction.response.edit_message(embed=embed, attachments=attachments, view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction, button):
        self.page = min(self.total_pages() - 1, self.page + 1)
        self.refresh_items()
        embed, file = self.build_message()
        attachments = [file] if file else []
        await interaction.response.edit_message(embed=embed, attachments=attachments, view=self)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, row=2)
    async def back_button(self, interaction, button):
        view = SubmitImageView(self.cog, self.author_id, self.team_name, self.player_name)
        await interaction.response.edit_message(embed=self.cog.build_submit_embed(self.team_name, self.player_name), attachments=[], view=view)


async def setup(bot):
    await bot.add_cog(Images(bot))
