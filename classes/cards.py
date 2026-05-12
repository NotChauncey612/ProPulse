import json
import io
import random
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote
import discord
from discord.ext import commands
from PIL import Image, ImageDraw, ImageOps

PLAYERS_PATH = "data/players.json"
CARDS_PATH = "data/cards.json"
USERS_PATH = "data/users.json"
PACKS_PATH = "data/packs.json"
AUCTIONS_PATH = "data/auctions.json"
AUCTIONS_HISTORY_PATH = "data/auctions_history.json"

CARDS_PER_PAGE = 20
RARITY_ORDER = ["Silver", "Gold", "Diamond", "Master", "Challenger"]
TEAM_ROLE_ORDER = ["TOP", "JNG", "MID", "BOT", "SUP"]
TEAM_ROLE_LABELS = {
    "TOP": "Top",
    "JNG": "Jungle",
    "MID": "Mid",
    "BOT": "Bot",
    "SUP": "Support",
}
TEAM_ROLE_ALIASES = {
    "top": "TOP",
    "jng": "JNG",
    "jg": "JNG",
    "jungle": "JNG",
    "mid": "MID",
    "middle": "MID",
    "bot": "BOT",
    "bottom": "BOT",
    "adc": "BOT",
    "sup": "SUP",
    "support": "SUP",
}
RARITY_RANK = {rarity: index for index, rarity in enumerate(RARITY_ORDER)}
RARITY_POWER = {
    "Silver": 80,
    "Gold": 100,
    "Diamond": 125,
    "Master": 150,
    "Challenger": 180,
}
DEFAULT_ELO = 1000
RANK_THRESHOLDS = [
    ("Challenger", 2200),
    ("Champ", 1800),
    ("Diamond", 1500),
    ("Gold", 1200),
    ("Silver", 0),
]
TEAM_EMOJI = "🏳️"
ROLE_EMOJI = "🎮"
LEAGUE_EMOJI = "🏆"
SET_EMOJI = "📦"
MISSING_CARD_SYMBOL = "⚫"


class InventoryView(discord.ui.View):
    def __init__(self, cog, author_id, user_display_name, owned_cards, filter_text=None):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.user_display_name = user_display_name
        self.owned_cards = owned_cards
        self.filter_text = filter_text
        self.page = 0
        self.update_buttons()

    def total_pages(self):
        if not self.owned_cards:
            return 1
        return (len(self.owned_cards) - 1) // CARDS_PER_PAGE + 1

    def get_page_slice(self):
        start = self.page * CARDS_PER_PAGE
        end = start + CARDS_PER_PAGE
        return start, end

    def build_embed(self):
        start, end = self.get_page_slice()
        page_cards = self.owned_cards[start:end]

        lines = self.cog.build_inventory_lines(page_cards)
        description = "\n".join(lines) if lines else "No cards matched those filters."

        if self.filter_text:
            description = f"Filters: {self.filter_text}\n\n{description}"

        embed = discord.Embed(
            title=f"{self.user_display_name}'s Inventory",
            description=description,
            color=discord.Color.dark_grey()
        )
        embed.set_footer(
            text=f"Page {self.page + 1}/{self.total_pages()} • {len(self.owned_cards)} total cards"
        )
        return embed

    def update_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages() - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You cannot use someone else's inventory buttons.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="◀ Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next ▶", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class ProgressView(discord.ui.View):
    def __init__(self, cog, author_id, user_display_name, progress_data):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.user_display_name = user_display_name
        self.progress_data = progress_data
        self.page = 0
        self.update_buttons()

    def total_pages(self):
        cards = self.progress_data["cards"]
        if not cards:
            return 1
        return (len(cards) - 1) // CARDS_PER_PAGE + 1

    def get_page_slice(self):
        start = self.page * CARDS_PER_PAGE
        end = start + CARDS_PER_PAGE
        return self.progress_data["cards"][start:end]

    def build_card_line(self, card_data):
        card_id = card_data.get("card_id", card_data.get("id"))
        best_rarity = self.progress_data["best_rarities"].get(card_id)
        symbol = self.cog.get_rarity_symbol(best_rarity) if best_rarity else MISSING_CARD_SYMBOL
        info_number = self.progress_data["card_info_numbers"].get(card_id)
        player = self.cog.get_player_for_card(card_data) or {}
        player_name = player.get("name", card_data.get("ign", "Unknown"))
        set_name = card_data.get("set", "Unknown Set")
        info_label = f"`CID {info_number}`" if info_number else "`CID ?`"

        if best_rarity:
            return f"{symbol} {info_label} {player_name} - {set_name} - {best_rarity}"
        return f"{symbol} {info_label} {player_name} - {set_name}"

    def build_embed(self):
        collection_name = self.progress_data["collection_name"]
        owned_count = self.progress_data["owned_count"]
        total_count = self.progress_data["total_count"]
        percentage = self.progress_data["percentage"]
        completion_text = self.progress_data["completion_text"]

        header_lines = [
            f"You are **{percentage}%** complete collecting **{collection_name}**.",
            f"Collected: **{owned_count}/{total_count}**"
        ]
        if completion_text:
            header_lines.insert(0, f"**{completion_text}**")

        card_lines = [self.build_card_line(card) for card in self.get_page_slice()]
        description = "\n".join(header_lines)
        if card_lines:
            description = f"{description}\n\n" + "\n".join(card_lines)

        embed = discord.Embed(
            title=f"{self.user_display_name}'s Progress",
            description=description,
            color=discord.Color.dark_grey()
        )
        embed.set_footer(
            text=f"Page {self.page + 1}/{self.total_pages()} - {total_count} cards in collection"
        )
        return embed

    def update_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages() - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You cannot use someone else's progress buttons.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class CardInfoListView(discord.ui.View):
    def __init__(self, cog, author_id, indexed_cards, rarity_counts, filters=None):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.indexed_cards = indexed_cards
        self.rarity_counts = rarity_counts
        self.filters = filters or {}
        self.page = 0
        self.update_buttons()

    def total_pages(self):
        if not self.indexed_cards:
            return 1
        return (len(self.indexed_cards) - 1) // CARDS_PER_PAGE + 1

    def get_page_slice(self):
        start = self.page * CARDS_PER_PAGE
        end = start + CARDS_PER_PAGE
        return self.indexed_cards[start:end]

    def build_embed(self):
        lines = []
        for cid, card_data in self.get_page_slice():
            player = self.cog.get_player_for_card(card_data) or {}
            lines.append(
                f"`CID {cid}` {player.get('name', card_data.get('ign', 'Unknown'))} - "
                f"{TEAM_EMOJI} {card_data.get('team', 'Unknown')} - "
                f"{ROLE_EMOJI} {player.get('role', card_data.get('role', 'Unknown'))} - "
                f"{LEAGUE_EMOJI} {card_data.get('league', 'Unknown')} - "
                f"{SET_EMOJI} {card_data.get('set', 'Unknown Set')}"
            )

        description = "\n".join(lines) if lines else "No cards matched those filters."
        filter_text = self.cog.build_filter_text(self.filters)
        if filter_text:
            description = f"Filters: {filter_text}\n\n{description}"

        embed = discord.Embed(
            title="Card Info",
            description=description,
            color=discord.Color.dark_grey()
        )
        embed.set_footer(
            text=f"Page {self.page + 1}/{self.total_pages()} - {len(self.indexed_cards)} matching cards - Use .info <CID> to view a card"
        )
        return embed

    def update_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages() - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You cannot use someone else's info buttons.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class CardInfoView(discord.ui.View):
    def __init__(self, cog, author_id, cards, rarity_counts, filters=None):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.cards = cards
        self.rarity_counts = rarity_counts
        self.filters = filters or {}
        self.page = 0
        self.update_buttons()

    def total_pages(self):
        return max(1, len(self.cards))

    def build_embed(self):
        if not self.cards:
            description = "No cards matched those filters."
            filter_text = self.cog.build_filter_text(self.filters)
            if filter_text:
                description = f"Filters: {filter_text}\n\n{description}"
            return discord.Embed(title="Card Info", description=description, color=discord.Color.dark_grey())

        cid = None
        card_data = self.cards[self.page]
        if isinstance(card_data, tuple):
            cid, card_data = card_data
        player = self.cog.get_player_for_card(card_data) or {}
        counts = self.rarity_counts.get(card_data.get("card_id"), {})
        total_pulled = sum(counts.values())
        rarity_lines = []

        for rarity in RARITY_ORDER:
            rarity_lines.append(f"{self.cog.get_rarity_symbol(rarity)} {rarity}: {counts.get(rarity, 0)}")

        for rarity, count in sorted(counts.items()):
            if rarity not in RARITY_ORDER:
                rarity_lines.append(f"{self.cog.get_rarity_symbol(rarity)} {rarity}: {count}")

        description_parts = []
        filter_text = self.cog.build_filter_text(self.filters)
        if filter_text:
            description_parts.append(f"Filters: {filter_text}")
        detail_lines = [
            f"CID: `{cid}`" if cid is not None else None,
            f"{TEAM_EMOJI} Team: {card_data.get('team', 'Unknown')}",
            f"{ROLE_EMOJI} Role: {player.get('role', card_data.get('role', 'Unknown'))}",
            f"{LEAGUE_EMOJI} League: {card_data.get('league', 'Unknown')}",
            f"{SET_EMOJI} Set: {card_data.get('set', 'Unknown Set')}",
        ]
        detail_lines.append(f"Card ID: `{card_data.get('card_id', card_data.get('id', 'unknown'))}`")
        description_parts.append("\n".join(line for line in detail_lines if line is not None))

        embed = discord.Embed(
            title=player.get("name", card_data.get("ign", "Unknown Card")),
            description="\n\n".join(description_parts),
            color=discord.Color.dark_grey()
        )
        embed.add_field(name="Pulled By Rarity", value="\n".join(rarity_lines), inline=False)
        embed.add_field(name="Total Pulled", value=str(total_pulled), inline=True)

        image_url = card_data.get("image_url", card_data.get("image", ""))
        if image_url:
            embed.set_image(url=image_url)

        embed.set_footer(text=f"Card {self.page + 1}/{self.total_pages()} - {len(self.cards)} selected cards")
        return embed

    def update_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages() - 1

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You cannot use someone else's info buttons.",
                ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class TeamRoleSelect(discord.ui.Select):
    def __init__(self, view):
        options = [
            discord.SelectOption(
                label=TEAM_ROLE_LABELS[role],
                value=role,
                description=f"Replace your {TEAM_ROLE_LABELS[role]} card"
            )
            for role in TEAM_ROLE_ORDER
        ]
        super().__init__(
            placeholder="Choose a role to replace",
            min_values=1,
            max_values=1,
            options=options
        )
        self.team_view = view

    async def callback(self, interaction: discord.Interaction):
        role = self.values[0]
        await interaction.response.send_modal(TeamCardModal(self.team_view, role))


class TeamView(discord.ui.View):
    def __init__(self, cog, author_id, user_display_name):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id
        self.user_display_name = user_display_name
        self.message = None
        self.add_item(TeamRoleSelect(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message(
                "You cannot edit someone else's team.",
                ephemeral=True
            )
            return False
        return True

    def build_embed(self):
        return self.cog.build_team_embed(self.author_id, self.user_display_name)

    def build_embeds(self):
        return self.cog.build_team_embeds(self.author_id, self.user_display_name)

    def build_message(self):
        return self.cog.build_team_message(self.author_id, self.user_display_name)

    async def refresh_message(self):
        if self.message:
            embed, file = self.build_message()
            attachments = [file] if file else []
            await self.message.edit(embed=embed, attachments=attachments, view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        await self.refresh_message()


class TeamCardModal(discord.ui.Modal, title="Set Team Card"):
    card_id = discord.ui.TextInput(
        label="Card ID or Inventory Number",
        placeholder="Example: 2026_lck_t1_faker or 12"
    )

    def __init__(self, team_view, role):
        super().__init__()
        self.team_view = team_view
        self.role = role

    async def on_submit(self, interaction: discord.Interaction):
        message = self.team_view.cog.set_team_card(
            interaction.user.id,
            self.role,
            str(self.card_id.value).strip()
        )
        await interaction.response.send_message(message, ephemeral=True)
        await self.team_view.refresh_message()


class Cards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = self.load_json(PLAYERS_PATH, default=[])
        self.cards = self.load_cards()
        self.card_aliases = self.build_card_aliases()
        self.packs = self.load_packs()

    # -----------------
    # JSON helpers
    # -----------------

    def load_json(self, path, default):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def load_users(self):
        users_cog = self.bot.get_cog("Users") if self.bot else None
        if users_cog is not None:
            return users_cog.users
        return self.load_json(USERS_PATH, default={})

    def save_users(self, users_data):
        users_cog = self.bot.get_cog("Users") if self.bot else None
        if users_cog is not None:
            users_cog.users = users_data
            users_cog.save_users()
            return

        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(users_data, f, indent=4)

    def load_cards(self):
        raw = self.load_json(CARDS_PATH, default={})

        # Legacy format: [{"id": "...", "player_id": "...", ...}]
        if isinstance(raw, list):
            indexed = {}
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                card_id = entry.get("card_id") or entry.get("id")
                if card_id:
                    indexed[card_id] = entry
            return indexed

        indexed = {}
        games = raw.get("games", {}) if isinstance(raw, dict) else {}

        for game_name, game_data in games.items():
            sets = game_data.get("sets", {}) if isinstance(game_data, dict) else {}
            for set_name, set_data in sets.items():
                leagues = set_data.get("leagues", {}) if isinstance(set_data, dict) else {}
                for league_name, league_data in leagues.items():
                    teams = league_data.get("teams", {}) if isinstance(league_data, dict) else {}
                    for team_name, team_data in teams.items():
                        cards = team_data.get("cards", []) if isinstance(team_data, dict) else []
                        for card in cards:
                            if not isinstance(card, dict):
                                continue
                            card_id = card.get("card_id")
                            if not card_id:
                                continue

                            normalized = dict(card)
                            normalized.setdefault("game", game_name)
                            normalized.setdefault("set", set_name)
                            normalized.setdefault("league", league_name)
                            normalized.setdefault("team", team_name)
                            normalized.setdefault("image_url", normalized.get("image", ""))
                            normalized.setdefault("id", card_id)
                            normalized.setdefault("player_id", card_id)
                            indexed[card_id] = normalized

        return indexed

    def _slug(self, value):
        return "".join(ch for ch in str(value).lower() if ch.isalnum())

    def _set_year_tokens(self, set_name):
        digits = "".join(ch for ch in str(set_name) if ch.isdigit())
        if len(digits) >= 4:
            return {digits[-4:], digits[-2:]}
        if len(digits) >= 2:
            yy = digits[-2:]
            return {yy, f"20{yy}"}
        return set()

    def build_card_aliases(self):
        aliases = {}

        for card_id, card in self.cards.items():
            ign = self._slug(card.get("ign", ""))
            league = self._slug(card.get("league", ""))
            game = self._slug(card.get("game", ""))
            years = self._set_year_tokens(card.get("set", ""))

            if ign and league:
                for year in years:
                    aliases[f"{ign}_{league}_{year}"] = card_id

            if ign and game:
                for year in years:
                    aliases[f"{ign}_{game}_{year}"] = card_id

            aliases[self._slug(card_id)] = card_id

        return aliases

    def load_packs(self):
        data = self.load_json(PACKS_PATH, default={})

        if isinstance(data, dict) and "packs" in data:
            packs = data["packs"]
        elif isinstance(data, list):
            packs = data
        else:
            packs = []

        indexed = {}
        for pack in packs:
            if not isinstance(pack, dict):
                continue
            pack_id = pack.get("pack_id")
            if pack_id:
                indexed[pack_id] = pack
        return indexed

    # -----------------
    # Lookup helpers
    # -----------------

    def get_player_by_id(self, player_id):
        for player in self.players:
            if player.get("id") == player_id:
                return player
        return None

    def get_card_by_id(self, card_id):
        if card_id in self.cards:
            return self.cards[card_id]

        alias_target = self.card_aliases.get(str(card_id).lower()) or self.card_aliases.get(self._slug(card_id))
        if alias_target:
            return self.cards.get(alias_target)
        return None

    def get_player_for_card(self, card_data):
        if not card_data:
            return None

        legacy_player = self.get_player_by_id(card_data.get("player_id"))
        if legacy_player:
            return legacy_player

        return {
            "id": card_data.get("card_id", card_data.get("id", "unknown")),
            "name": card_data.get("ign", "Unknown"),
            "role": card_data.get("role", "Unknown")
        }

    def get_user_data(self, user_id):
        users = self.load_users()
        return users, users.get(str(user_id))

    # -----------------
    # Card generation
    # -----------------

    def roll_rarity(self):
        roll = random.randint(1, 100)
        if roll <= 50:
            return "Silver"
        if roll <= 80:
            return "Gold"
        if roll <= 95:
            return "Diamond"
        if roll <= 99:
            return "Master"
        return "Challenger"

    def create_card_instance(self, card_id, card_data=None, pulled_by_user=None):
        instance = {
            "instance_id": str(uuid.uuid4()),
            "card_id": card_id,
            "rarity": self.roll_rarity(),
            "pulled_on": datetime.utcnow().isoformat()
        }
        if pulled_by_user is not None:
            instance["pulled_by_id"] = str(pulled_by_user.id)
            instance["pulled_by_username"] = pulled_by_user.name
        if card_data:
            instance["snapshot"] = {
                "ign": card_data.get("ign", "Unknown"),
                "team": card_data.get("team", "Unknown"),
                "set": card_data.get("set", "Unknown Set"),
                "league": card_data.get("league", "Unknown"),
                "image_url": card_data.get("image_url", card_data.get("image", ""))
            }
        return instance

    def add_card_to_user(self, users, user_id, card_instance):
        uid = str(user_id)
        users[uid].setdefault("cards", [])
        slots = users[uid]["cards"]
        placed = False
        for i, existing in enumerate(slots):
            if existing is None:
                slots[i] = card_instance
                placed = True
                break
        if not placed:
            slots.append(card_instance)
        self.save_users(users)

    def remove_card_from_user(self, users, user_id, card_instance):
        uid = str(user_id)
        slots = users.get(uid, {}).get("cards", [])
        target_id = card_instance.get("instance_id")
        for i, existing in enumerate(slots):
            if not isinstance(existing, dict):
                continue
            if existing.get("instance_id") == target_id:
                slots[i] = None
                self.save_users(users)
                return True
        return False

    def pull_random_card_for_user(self, user):
        user_id = user.id
        users, user_data = self.get_user_data(user_id)

        if user_data is None:
            return None, None, None, "You need to create a profile first with `.join`."

        if not self.cards:
            return None, None, None, "No cards are loaded."

        chosen_card = random.choice(list(self.cards.values()))
        player = self.get_player_for_card(chosen_card)

        if player is None:
            return None, None, None, "Card data is missing a valid player."

        card_instance = self.create_card_instance(chosen_card["card_id"], chosen_card, user)
        self.add_card_to_user(users, user_id, card_instance)

        return card_instance, chosen_card, player, None

    # -----------------
    # Inventory helpers
    # -----------------

    def parse_inventory_filters(self, args):
        filters = {}
        valid_flags = {"-team", "-rarity", "-player", "-set", "-role", "-league", "-region"}

        i = 0
        while i < len(args):
            current = args[i].lower()

            if current in valid_flags:
                key = current[1:]
                if key == "region":
                    key = "league"
                i += 1

                value_parts = []
                while i < len(args) and args[i].lower() not in valid_flags:
                    value_parts.append(args[i])
                    i += 1

                if value_parts:
                    filters[key] = " ".join(value_parts)
            else:
                i += 1

        return filters

    def card_matches_filters(self, owned_card, filters):
        card_data = self.get_card_by_id(owned_card.get("card_id"))
        if not card_data and owned_card.get("snapshot"):
            card_data = owned_card["snapshot"]
        if not card_data:
            return False

        player = self.get_player_for_card(card_data)
        if not player:
            return False

        if "team" in filters:
            if card_data.get("team", "").lower() != filters["team"].lower():
                return False

        if "rarity" in filters:
            if owned_card.get("rarity", "").lower() != filters["rarity"].lower():
                return False

        if "player" in filters:
            player_name = player.get("name", "")
            player_id = player.get("id", "")
            target = filters["player"].lower()

            if player_name.lower() != target and player_id.lower() != target:
                return False

        if "set" in filters:
            if card_data.get("set", "").lower() != filters["set"].lower():
                return False

        if "league" in filters:
            if card_data.get("league", "").lower() != filters["league"].lower():
                return False
        
        if "role" in filters:
            wanted_role = self.normalize_team_role(filters["role"])
            actual_role = self.normalize_team_role(player.get("role", ""))

            if wanted_role and actual_role:
                if wanted_role != actual_role:
                    return False
            elif filters["role"].lower() not in player.get("role", "").lower():
                return False

        return True

    def filter_owned_cards(self, owned_cards, filters):
        if not filters:
            return [(idx, card) for idx, card in enumerate(owned_cards, start=1) if isinstance(card, dict)]

        filtered = []

        for index, owned in enumerate(owned_cards, start=1):
            if not isinstance(owned, dict):
                continue
            if self.card_matches_filters(owned, filters):
                filtered.append((index, owned))

        return filtered

    def format_inventory_line(self, index, owned_card):
        card_data = self.get_card_by_id(owned_card.get("card_id"))
        if not card_data and owned_card.get("snapshot"):
            card_data = owned_card["snapshot"]
        if not card_data:
            return f"{index}. Unknown Card"

        player = self.get_player_for_card(card_data)
        if not player:
            return f"{index}. Unknown Player"

        player_name = player.get("name", "Unknown")
        set_name = card_data.get("set", "Unknown Set")
        rarity = owned_card.get("rarity", "Unknown Rarity")

        return f"{self.format_inventory_index(index)} {self.get_rarity_symbol(rarity)} {player_name} {set_name}"

    def build_inventory_lines(self, indexed_cards):
        lines = []

        for index, owned_card in indexed_cards:
            card_data = self.get_card_by_id(owned_card.get("card_id"))
            if not card_data and owned_card.get("snapshot"):
                card_data = owned_card["snapshot"]
            if not card_data:
                lines.append(f"{index}. Unknown Card")
                continue

            player = self.get_player_for_card(card_data)
            if not player:
                lines.append(f"{index}. Unknown Player")
                continue

            player_name = player.get("name", "Unknown")
            set_name = card_data.get("set", "Unknown Set")
            rarity = owned_card.get("rarity", "Unknown Rarity")

            lines.append(f"{self.format_inventory_index(index)} {self.get_rarity_symbol(rarity)} {player_name} {set_name}")

        return lines

    def build_filter_text(self, filters):
        if not filters:
            return None
        return " • ".join(f"{key.capitalize()}: {value}" for key, value in filters.items())

    def iter_card_instances_from_users(self):
        users = self.load_users()
        for user_data in users.values():
            if not isinstance(user_data, dict):
                continue
            for owned_card in user_data.get("cards", []):
                if isinstance(owned_card, dict):
                    yield owned_card

    def iter_card_instances_from_auctions(self):
        for path in (AUCTIONS_PATH, AUCTIONS_HISTORY_PATH):
            auctions = self.load_json(path, default=[])
            if not isinstance(auctions, list):
                continue
            for auction in auctions:
                if not isinstance(auction, dict) or auction.get("item_type") != "card":
                    continue
                card_instance = auction.get("card_instance")
                if isinstance(card_instance, dict):
                    yield card_instance

    def get_all_pulled_rarity_counts(self):
        counts = defaultdict(Counter)
        seen_instances = set()

        for card_instance in list(self.iter_card_instances_from_users()) + list(self.iter_card_instances_from_auctions()):
            instance_id = card_instance.get("instance_id")
            if instance_id:
                if instance_id in seen_instances:
                    continue
                seen_instances.add(instance_id)

            card_id = card_instance.get("card_id")
            rarity = card_instance.get("rarity")
            if card_id and rarity:
                counts[card_id][rarity] += 1

        return counts

    def card_definition_matches_filters(self, card_data, filters, rarity_counts):
        if not filters:
            return True

        non_rarity_filters = {key: value for key, value in filters.items() if key != "rarity"}
        if non_rarity_filters:
            fake_owned_card = {
                "card_id": card_data.get("card_id", card_data.get("id")),
                "snapshot": card_data
            }
            if not self.card_matches_filters(fake_owned_card, non_rarity_filters):
                return False

        if "rarity" in filters:
            target = filters["rarity"].lower()
            card_counts = rarity_counts.get(card_data.get("card_id"), {})
            if not any(rarity.lower() == target and count > 0 for rarity, count in card_counts.items()):
                return False

        return True

    def get_sorted_card_definitions(self):
        cards = list(self.cards.values())
        cards.sort(key=lambda card: (
            str(card.get("league", "")).lower(),
            str(card.get("team", "")).lower(),
            str(card.get("role", "")).lower(),
            str(card.get("ign", "")).lower(),
        ))
        return cards

    def get_filtered_card_info(self, args):
        filters = self.parse_inventory_filters(args)
        rarity_counts = self.get_all_pulled_rarity_counts()
        indexed_cards = [
            (index, card)
            for index, card in enumerate(self.get_sorted_card_definitions(), start=1)
            if self.card_definition_matches_filters(card, filters, rarity_counts)
        ]
        return indexed_cards, rarity_counts, filters

    def get_card_info_by_number(self, card_number):
        cards = self.get_sorted_card_definitions()
        if card_number < 1 or card_number > len(cards):
            return None
        return card_number, cards[card_number - 1]

    def get_progress_collection_name(self, filters, matching_cards):
        if "team" in filters:
            teams = sorted({card.get("team", "Unknown") for card in matching_cards})
            if len(teams) == 1:
                return teams[0]
            return filters["team"]

        if filters:
            return self.build_filter_text(filters) or "Selected Collection"

        return "All Cards"

    def progress_card_sort_key(self, card):
        role_order = {"TOP": 0, "JNG": 1, "MID": 2, "BOT": 3, "SUP": 4}
        return (
            str(card.get("league", "")).lower(),
            str(card.get("team", "")).lower(),
            role_order.get(str(card.get("role", "")).upper(), 99),
            str(card.get("role", "")).lower(),
            str(card.get("ign", "")).lower(),
        )

    def get_user_best_rarities(self, user_data):
        best_rarities = {}

        for owned_card in user_data.get("cards", []):
            if not isinstance(owned_card, dict):
                continue

            card_id = owned_card.get("card_id")
            rarity = owned_card.get("rarity")
            if not card_id or rarity not in RARITY_RANK:
                continue

            card_data = self.get_card_by_id(card_id)
            if card_data:
                card_id = card_data.get("card_id", card_id)

            current_best = best_rarities.get(card_id)
            if current_best is None or RARITY_RANK[rarity] > RARITY_RANK[current_best]:
                best_rarities[card_id] = rarity

        return best_rarities

    def get_completion_text(self, collection_name, matching_cards, best_rarities):
        if not matching_cards:
            return None

        for rarity in reversed(RARITY_ORDER):
            target_rank = RARITY_RANK[rarity]
            completed = all(
                RARITY_RANK.get(best_rarities.get(card.get("card_id", card.get("id"))), -1) >= target_rank
                for card in matching_cards
            )
            if completed:
                if rarity == "Silver":
                    return f"Silver Completed for {collection_name}"
                return f"{rarity} Level Completed for {collection_name}"

        return None

    def get_collection_progress(self, user_id, args):
        users, user_data = self.get_user_data(user_id)

        if user_data is None:
            return None, "You need to create a profile first with `.join`."

        filters = self.parse_inventory_filters(args)
        filters.pop("rarity", None)

        card_info_numbers = {
            card.get("card_id", card.get("id")): index
            for index, card in enumerate(self.get_sorted_card_definitions(), start=1)
        }

        matching_cards = [
            card
            for card in self.cards.values()
            if self.card_definition_matches_filters(card, filters, defaultdict(Counter))
        ]
        matching_cards.sort(
            key=lambda card: card_info_numbers.get(card.get("card_id", card.get("id")), float("inf"))
        )

        if not matching_cards:
            return None, "No cards matched that collection."

        best_rarities = self.get_user_best_rarities(user_data)
        owned_ids = {
            card.get("card_id", card.get("id"))
            for card in matching_cards
            if card.get("card_id", card.get("id")) in best_rarities
        }
        total_count = len(matching_cards)
        owned_count = len(owned_ids)
        percentage = round((owned_count / total_count) * 100)
        collection_name = self.get_progress_collection_name(filters, matching_cards)

        return {
            "cards": matching_cards,
            "card_info_numbers": card_info_numbers,
            "best_rarities": best_rarities,
            "owned_count": owned_count,
            "total_count": total_count,
            "percentage": percentage,
            "collection_name": collection_name,
            "completion_text": self.get_completion_text(collection_name, matching_cards, best_rarities),
            "filters": filters,
        }, None

    def format_inventory_index(self, index):
        return f"`#{index}`"

    def get_rarity_symbol(self, rarity):
        symbols = {
            "Silver": "⚪",
            "Gold": "🟡",
            "Diamond": "🟣",
            "Master": "🔴",
            "Challenger": "🔵"
        }
        return symbols.get(rarity, "⚫")

    def get_filtered_inventory(self, user_id, args):
        users, user_data = self.get_user_data(user_id)

        if user_data is None:
            return None, None, "You need to create a profile first with `.join`."

        owned_cards = user_data.get("cards", [])
        valid_cards = [c for c in owned_cards if isinstance(c, dict)]
        if not valid_cards:
            return None, None, "Your inventory is empty."

        filters = self.parse_inventory_filters(args)
        filtered_cards = self.filter_owned_cards(owned_cards, filters)

        if not filtered_cards:
            return None, None, "No cards matched those filters."

        return filtered_cards, filters, None

    def get_owned_card_by_inventory_number(self, user_id, inventory_number):
        users, user_data = self.get_user_data(user_id)

        if user_data is None:
            return None, None, None, "You need to create a profile first with `.join`."

        owned_cards = user_data.get("cards", [])
        if inventory_number < 1 or inventory_number > len(owned_cards):
            return None, None, None, "That inventory number does not exist."
        owned_card = owned_cards[inventory_number - 1]
        if not isinstance(owned_card, dict):
            return None, None, None, "Your inventory is empty."
        card_data = self.get_card_by_id(owned_card.get("card_id"))
        if not card_data and owned_card.get("snapshot"):
            card_data = owned_card["snapshot"]

        if not card_data:
            return None, None, None, "That card's data could not be found. (Legacy card without snapshot)"

        player = self.get_player_for_card(card_data)

        if not player:
            return None, None, None, "That card's player data could not be found."

        return owned_card, card_data, player, None

    # -----------------
    # Team helpers
    # -----------------

    def normalize_team_role(self, role):
        return TEAM_ROLE_ALIASES.get(str(role).strip().lower())

    def get_user_team(self, user_data):
        team = user_data.setdefault("team", {})
        if not isinstance(team, dict):
            team = {}
            user_data["team"] = team
        return team

    def resolve_team_card(self, user_data, instance_id):
        if not instance_id:
            return None, None, None, None

        for index, owned_card in enumerate(user_data.get("cards", []), start=1):
            if not isinstance(owned_card, dict):
                continue
            if owned_card.get("instance_id") != instance_id:
                continue

            card_data = self.get_card_by_id(owned_card.get("card_id"))
            if not card_data and owned_card.get("snapshot"):
                card_data = owned_card["snapshot"]
            if not card_data:
                return index, owned_card, None, None

            player = self.get_player_for_card(card_data)
            return index, owned_card, card_data, player

        return None, None, None, None

    def find_owned_card_for_team_input(self, user_data, card_input):
        owned_cards = user_data.get("cards", [])
        card_input = str(card_input).strip()

        if not card_input:
            return None, None, None, "Enter a card id or inventory number."

        if card_input.startswith("#"):
            card_input = card_input[1:]

        if card_input.isdigit():
            inventory_number = int(card_input)
            if inventory_number < 1 or inventory_number > len(owned_cards):
                return None, None, None, "That inventory number does not exist."
            owned_card = owned_cards[inventory_number - 1]
            if not isinstance(owned_card, dict):
                return None, None, None, "That inventory slot is empty."

            card_data = self.get_card_by_id(owned_card.get("card_id"))
            if not card_data and owned_card.get("snapshot"):
                card_data = owned_card["snapshot"]
            if not card_data:
                return None, None, None, "That card's data could not be found."
            return inventory_number, owned_card, card_data, None

        matches = []
        target = self._slug(card_input)
        for index, owned_card in enumerate(owned_cards, start=1):
            if not isinstance(owned_card, dict):
                continue

            instance_id = owned_card.get("instance_id", "")
            card_id = owned_card.get("card_id", "")
            card_data = self.get_card_by_id(card_id)
            if not card_data and owned_card.get("snapshot"):
                card_data = owned_card["snapshot"]
            if not card_data:
                continue

            player = self.get_player_for_card(card_data) or {}
            identifiers = {
                self._slug(instance_id),
                self._slug(card_id),
                self._slug(player.get("name", "")),
                self._slug(card_data.get("ign", "")),
            }
            if target in identifiers:
                matches.append((index, owned_card, card_data))

        if not matches:
            return None, None, None, "You do not own that card."
        if len(matches) > 1:
            return None, None, None, "You own multiple matching cards. Use the inventory number from `.inv`."

        index, owned_card, card_data = matches[0]
        return index, owned_card, card_data, None

    def build_team_embed(self, user_id, user_display_name):
        embed, _ = self.build_team_message(user_id, user_display_name)
        return embed

    def build_team_embeds(self, user_id, user_display_name):
        return [self.build_team_embed(user_id, user_display_name)]

    def format_team_set_label(self, card_data):
        league = str(card_data.get("league", "")).strip()
        set_name = str(card_data.get("set", "")).strip()
        year = ""

        if "'" in set_name:
            year = set_name[set_name.rfind("'"):]
        else:
            digits = "".join(ch for ch in set_name if ch.isdigit())
            if len(digits) >= 2:
                year = f"'{digits[-2:]}"

        if league and year:
            return f"{league} {year}"
        return set_name or league or "Unknown Set"

    def get_local_card_image_path(self, card_data):
        image_url = card_data.get("image_url", card_data.get("image", ""))
        if not image_url:
            return None

        if "/main/" in image_url:
            candidate = Path(unquote(image_url.split("/main/", 1)[1]))
            if candidate.exists():
                return candidate

        candidate = Path(unquote(image_url))
        if candidate.exists():
            return candidate

        filename = Path(unquote(image_url)).name
        if filename:
            matches = list(Path("player_images").rglob(filename))
            if matches:
                return matches[0]

        return None

    def get_rarity_rgb(self, rarity):
        colors = {
            "Silver": (192, 198, 207),
            "Gold": (235, 190, 68),
            "Diamond": (146, 103, 236),
            "Master": (224, 82, 82),
            "Challenger": (74, 144, 226),
        }
        return colors.get(rarity, (128, 136, 148))

    def fit_line(self, draw, text, max_width):
        original = str(text)
        if draw.textbbox((0, 0), original)[2] <= max_width:
            return original

        text = original
        while text and draw.textbbox((0, 0), f"{text}...")[2] > max_width:
            text = text[:-1]
        return f"{text}..." if text else ""

    def draw_centered_text(self, draw, box, text, fill):
        left, top, right, bottom = box
        text_box = draw.textbbox((0, 0), text)
        text_width = text_box[2] - text_box[0]
        text_height = text_box[3] - text_box[1]
        x = left + ((right - left) - text_width) // 2
        y = top + ((bottom - top) - text_height) // 2
        draw.text((x, y), text, fill=fill)

    def make_team_lineup_file(self, slots, user_id):
        if not slots:
            return None

        card_width = 140
        image_height = 168
        header_height = 44
        footer_height = 32
        gap = 10
        padding = 12
        width = padding * 2 + len(TEAM_ROLE_ORDER) * card_width + (len(TEAM_ROLE_ORDER) - 1) * gap
        height = padding * 2 + header_height + image_height + footer_height

        canvas = Image.new("RGB", (width, height), (31, 34, 40))
        draw = ImageDraw.Draw(canvas)

        slot_by_role = {slot["role"]: slot for slot in slots}
        for column, role in enumerate(TEAM_ROLE_ORDER):
            x = padding + column * (card_width + gap)
            y = padding
            draw.rounded_rectangle(
                (x, y, x + card_width, y + header_height + image_height + footer_height),
                radius=8,
                fill=(43, 47, 54),
                outline=(82, 88, 99)
            )

            slot = slot_by_role.get(role)
            if slot:
                image_path = self.get_local_card_image_path(slot["card_data"])
                rarity_color = self.get_rarity_rgb(slot["rarity"])
                header = self.fit_line(
                    draw,
                    f"{TEAM_ROLE_LABELS[role]} - {slot['player_name']}",
                    card_width - 14
                )
                set_label = self.fit_line(draw, slot["set_label"], card_width - 14)
            else:
                image_path = None
                rarity_color = (82, 88, 99)
                header = TEAM_ROLE_LABELS[role]
                set_label = "Empty"

            self.draw_centered_text(
                draw,
                (x + 6, y + 5, x + card_width - 6, y + header_height - 5),
                header,
                (238, 240, 244)
            )

            image_y = y + header_height
            border_width = 4
            draw.rounded_rectangle(
                (x + 5, image_y, x + card_width - 5, image_y + image_height),
                radius=6,
                outline=rarity_color,
                width=border_width,
                fill=(27, 29, 34)
            )
            image_box = (
                x + 5 + border_width,
                image_y + border_width,
                x + card_width - 5 - border_width,
                image_y + image_height - border_width,
            )
            inner_width = image_box[2] - image_box[0]
            inner_height = image_box[3] - image_box[1]
            if image_path:
                with Image.open(image_path) as card_image:
                    card_image = ImageOps.contain(card_image.convert("RGB"), (inner_width, inner_height))
                    paste_x = image_box[0] + (inner_width - card_image.width) // 2
                    paste_y = image_box[1] + (inner_height - card_image.height) // 2
                    canvas.paste(card_image, (paste_x, paste_y))
            else:
                self.draw_centered_text(draw, image_box, "Empty", (170, 176, 186))

            self.draw_centered_text(
                draw,
                (x + 6, image_y + image_height + 4, x + card_width - 6, y + header_height + image_height + footer_height - 4),
                set_label,
                (210, 215, 224)
            )

        buffer = io.BytesIO()
        canvas.save(buffer, format="PNG")
        buffer.seek(0)
        return discord.File(buffer, filename=f"team_{user_id}.png")

    def build_team_message(self, user_id, user_display_name):
        users, user_data = self.get_user_data(user_id)
        if user_data is None:
            return discord.Embed(
                title=f"{user_display_name}'s Team",
                description="You need to create a profile first with `.join`.",
                color=discord.Color.dark_grey()
            ), None

        team = self.get_user_team(user_data)
        image_slots = []
        changed = False

        for role in TEAM_ROLE_ORDER:
            instance_id = team.get(role)
            index, owned_card, card_data, player = self.resolve_team_card(user_data, instance_id)
            if not owned_card:
                if instance_id:
                    team.pop(role, None)
                    changed = True
                continue

            if not card_data or not player:
                continue

            rarity = owned_card.get("rarity", "Unknown Rarity")
            player_name = player.get("name", card_data.get("ign", "Unknown"))
            set_label = self.format_team_set_label(card_data)

            image_slots.append({
                "role": role,
                "card_data": card_data,
                "player_name": player_name,
                "rarity": rarity,
                "set_label": set_label,
            })

        if changed:
            self.save_users(users)

        embed = discord.Embed(
            title=f"{user_display_name}'s Team",
            color=discord.Color.dark_grey()
        )
        embed.set_footer(text="Use the dropdown to choose a role, then enter a card id or inventory number.")
        file = self.make_team_lineup_file(image_slots, user_id)
        if file:
            embed.set_image(url=f"attachment://{file.filename}")
        return embed, file

    def set_team_card(self, user_id, role, card_input):
        users, user_data = self.get_user_data(user_id)
        if user_data is None:
            return "You need to create a profile first with `.join`."

        role = self.normalize_team_role(role)
        if not role:
            return "That is not a valid team role."

        index, owned_card, card_data, error = self.find_owned_card_for_team_input(user_data, card_input)
        if error:
            return error

        player = self.get_player_for_card(card_data)
        card_role = self.normalize_team_role((player or {}).get("role", card_data.get("role", "")))
        if card_role != role:
            wanted = TEAM_ROLE_LABELS[role]
            actual = TEAM_ROLE_LABELS.get(card_role, card_data.get("role", "Unknown"))
            return f"You can't do that. This card is {actual}, not {wanted}."

        team = self.get_user_team(user_data)
        instance_id = owned_card.get("instance_id")
        for existing_role, existing_instance_id in team.items():
            if existing_role != role and existing_instance_id == instance_id:
                return f"That card is already in your {TEAM_ROLE_LABELS.get(existing_role, existing_role)} slot."

        team[role] = instance_id
        self.save_users(users)

        player_name = player.get("name", card_data.get("ign", "Unknown"))
        return f"{TEAM_ROLE_LABELS[role]} updated to {player_name} from inventory #{index}."

    # -----------------
    # Ranked helpers
    # -----------------

    def xp_for_level(self, level):
        return max(0, (level - 1) * level * 50)

    def level_for_xp(self, xp):
        level = 1
        while self.xp_for_level(level + 1) <= xp:
            level += 1
        return level

    def normalize_ranked_profile(self, user_data):
        user_data.setdefault("xp", 0)
        user_data["level"] = self.level_for_xp(int(user_data.get("xp", 0)))
        user_data.setdefault("elo", DEFAULT_ELO)
        user_data.setdefault("ranked_wins", 0)
        user_data.setdefault("ranked_losses", 0)
        user_data.setdefault("team", {})

    def rank_for_elo(self, elo):
        for rank_name, threshold in RANK_THRESHOLDS:
            if elo >= threshold:
                return rank_name
        return "Silver"

    def get_ranked_team_slots(self, user_data):
        slots = []
        missing = []

        for role in TEAM_ROLE_ORDER:
            instance_id = self.get_user_team(user_data).get(role)
            index, owned_card, card_data, player = self.resolve_team_card(user_data, instance_id)
            if not owned_card or not card_data or not player:
                missing.append(TEAM_ROLE_LABELS[role])
                continue

            slots.append({
                "role": role,
                "index": index,
                "owned_card": owned_card,
                "card_data": card_data,
                "player": player,
            })

        return slots, missing

    def ranked_team_power(self, user_data, slots):
        level = int(user_data.get("level", 1))
        card_power = sum(
            RARITY_POWER.get(slot["owned_card"].get("rarity"), 70)
            for slot in slots
        )
        level_bonus = level * 15
        return card_power + level_bonus

    def find_ranked_opponent(self, users, user_id, user_elo):
        candidates = []
        search_windows = [100, 200, 400, 800, 9999]

        for window in search_windows:
            candidates.clear()
            for opponent_id, opponent_data in users.items():
                if str(opponent_id) == str(user_id) or not isinstance(opponent_data, dict):
                    continue

                self.normalize_ranked_profile(opponent_data)
                slots, missing = self.get_ranked_team_slots(opponent_data)
                if missing:
                    continue

                opponent_elo = int(opponent_data.get("elo", DEFAULT_ELO))
                if abs(opponent_elo - user_elo) <= window:
                    candidates.append((opponent_id, opponent_data, slots))

            if candidates:
                return random.choice(candidates)

        return None

    def expected_elo_score(self, player_elo, opponent_elo):
        return 1 / (1 + 10 ** ((opponent_elo - player_elo) / 400))

    def run_ranked_match(self, user_id):
        users, user_data = self.get_user_data(user_id)
        if user_data is None:
            return None, "You need to create a profile first with `.join`."

        self.normalize_ranked_profile(user_data)
        user_slots, missing = self.get_ranked_team_slots(user_data)
        if missing:
            return None, "You need a full team before playing ranked. Missing: " + ", ".join(missing)

        user_elo_before = int(user_data.get("elo", DEFAULT_ELO))
        opponent_entry = self.find_ranked_opponent(users, user_id, user_elo_before)
        if opponent_entry is None:
            return None, "No other users with a full team are available for ranked yet."

        opponent_id, opponent_data, opponent_slots = opponent_entry
        opponent_elo_before = int(opponent_data.get("elo", DEFAULT_ELO))
        user_power = self.ranked_team_power(user_data, user_slots)
        opponent_power = self.ranked_team_power(opponent_data, opponent_slots)

        win_chance = 1 / (1 + 10 ** ((opponent_power - user_power) / 300))
        user_won = random.random() < win_chance

        expected = self.expected_elo_score(user_elo_before, opponent_elo_before)
        if user_won:
            elo_delta = max(10, round(32 * (1 - expected)))
        else:
            elo_delta = min(-10, round(32 * (0 - expected)))

        old_rank = self.rank_for_elo(user_elo_before)
        opponent_old_rank = self.rank_for_elo(opponent_elo_before)
        user_data["elo"] = max(0, user_elo_before + elo_delta)
        opponent_data["elo"] = max(0, opponent_elo_before - elo_delta)

        if user_won:
            user_data["ranked_wins"] += 1
            opponent_data["ranked_losses"] += 1
        else:
            user_data["ranked_losses"] += 1
            opponent_data["ranked_wins"] += 1

        new_rank = self.rank_for_elo(user_data["elo"])
        opponent_new_rank = self.rank_for_elo(opponent_data["elo"])
        self.save_users(users)

        return {
            "opponent_id": opponent_id,
            "opponent_name": opponent_data.get("discord_username") or f"User {opponent_id}",
            "user_won": user_won,
            "win_chance": win_chance,
            "elo_delta": elo_delta,
            "user_elo_before": user_elo_before,
            "user_elo_after": user_data["elo"],
            "opponent_elo_before": opponent_elo_before,
            "opponent_elo_after": opponent_data["elo"],
            "user_power": user_power,
            "opponent_power": opponent_power,
            "old_rank": old_rank,
            "new_rank": new_rank,
            "opponent_old_rank": opponent_old_rank,
            "opponent_new_rank": opponent_new_rank,
            "wins": user_data["ranked_wins"],
            "losses": user_data["ranked_losses"],
        }, None

    def ranked_result_embed(self, ctx, result):
        title = "Ranked Victory" if result["user_won"] else "Ranked Defeat"
        color = discord.Color.green() if result["user_won"] else discord.Color.red()
        delta = result["elo_delta"]
        delta_text = f"+{delta}" if delta > 0 else str(delta)

        embed = discord.Embed(title=title, color=color)
        embed.add_field(name="Opponent", value=result["opponent_name"], inline=True)
        embed.add_field(name="Record", value=f"{result['wins']}W - {result['losses']}L", inline=True)
        embed.add_field(name="ELO", value=f"{result['user_elo_before']} -> {result['user_elo_after']} ({delta_text})", inline=False)
        embed.add_field(name="Rank", value=f"{result['old_rank']} -> {result['new_rank']}", inline=True)
        embed.add_field(name="Team Power", value=f"{result['user_power']} vs {result['opponent_power']}", inline=True)
        embed.add_field(name="Win Chance", value=f"{round(result['win_chance'] * 100)}%", inline=True)
        embed.set_footer(text="Practice gives XP. Higher levels add team stats for ranked.")
        return embed
    # -----------------
    # Embed helpers
    # -----------------

    def get_rarity_color(self, rarity):
        colors = {
            "Silver": discord.Color.light_grey(),
            "Gold": discord.Color.gold(),
            "Diamond": discord.Color.purple(),
            "Master": discord.Color.red(),
            "Challenger": discord.Color.blue(),
        }
        return colors.get(rarity, discord.Color.default())

    def card_embed(self, player, card_data, card_instance, pulled_by_name):
        rarity = card_instance.get("rarity", "Unknown")
        rarity_label = f"{self.get_rarity_symbol(rarity)} {rarity}"

        embed = discord.Embed(
            title=player.get("name", "Unknown"),
            color=self.get_rarity_color(rarity)
        )

        embed.add_field(name=f"{TEAM_EMOJI} Team", value=card_data.get("team", "Unknown"), inline=False)
        embed.add_field(name=f"{ROLE_EMOJI} Role", value=player.get("role", "Unknown"), inline=False)
        embed.add_field(name=f"{LEAGUE_EMOJI} League", value=card_data.get("league", "Unknown"), inline=False)
        embed.add_field(name=f"{SET_EMOJI} Set", value=card_data.get("set", "Unknown Set"), inline=False)
        embed.add_field(name="Rarity", value=rarity_label, inline=False)

        image_url = card_data.get("image_url", "")
        if image_url:
            embed.set_image(url=image_url)

        embed.set_footer(text=f"Pulled by {pulled_by_name}")
        return embed

    def build_inventory_view(self, author_id, user_display_name, owned_cards, filters):
        filter_text = self.build_filter_text(filters)
        return InventoryView(self, author_id, user_display_name, owned_cards, filter_text)

    # -----------------
    # Pack Helpers
    # -----------------

    def open_pack(self, user_id, pack_name, pulled_by_user=None):
        pack = self.packs.get(pack_name)

        if not pack:
            return None, "Pack not found."

        users, user_data = self.get_user_data(user_id)
        if user_data is None:
            return None, "You need to create a profile first with `.join`."

        set_name = pack.get("set")
        league = pack.get("league")
        leagues = pack.get("leagues", [])
        num_cards = pack.get("cards_per_pack", 0)

        pool = []
        for card in self.cards.values():
            if set_name and card.get("set") != set_name:
                continue
            if league and card.get("league") != league:
                continue
            if leagues and card.get("league") not in leagues:
                continue
            pool.append(card)

        if not pool:
            return None, "No cards found for that set."

        results = []

        for _ in range(num_cards):
            chosen_card = random.choice(pool)
            player = self.get_player_for_card(chosen_card)

            card_instance = self.create_card_instance(chosen_card["card_id"], chosen_card, pulled_by_user)
            self.add_card_to_user(users, user_id, card_instance)

            results.append((card_instance, chosen_card, player))

        return results, None

    # -----------------
    # Commands
    # -----------------


    # Testing command to pull a card without cooldowns or costs
    @commands.command()
    async def pull(self, ctx):
        card_instance, card_data, player, error = self.pull_random_card_for_user(ctx.author)

        if error:
            await ctx.send(error)
            return

        embed = self.card_embed(
            player=player,
            card_data=card_data,
            card_instance=card_instance,
            pulled_by_name=ctx.author.name
        )
        await ctx.send(embed=embed)


    # Inventory command to view all users cards with filtering options
    # EX: `.inventory -team T1 -rarity Gold` to show only gold cards of T1 players, or `.inventory` to show all cards
    @commands.command(aliases=["inv"])
    async def inventory(self, ctx, *args):
        filtered_cards, filters, error = self.get_filtered_inventory(ctx.author.id, args)

        if error:
            await ctx.send(error)
            return

        view = self.build_inventory_view(
            author_id=ctx.author.id,
            user_display_name=ctx.author.display_name,
            owned_cards=filtered_cards,
            filters=filters
        )

        await ctx.send(embed=view.build_embed(), view=view)

    # Progress command to show checklist completion for a collection.
    # EX: `.progress -team T1`
    @commands.command()
    async def progress(self, ctx, *args):
        progress_data, error = self.get_collection_progress(ctx.author.id, args)

        if error:
            await ctx.send(error)
            return

        view = ProgressView(
            cog=self,
            author_id=ctx.author.id,
            user_display_name=ctx.author.display_name,
            progress_data=progress_data
        )

        await ctx.send(embed=view.build_embed(), view=view)

    @commands.command()
    async def team(self, ctx):
        users, user_data = self.get_user_data(ctx.author.id)
        if user_data is None:
            await ctx.send("You need to create a profile first with `.join`.")
            return

        self.get_user_team(user_data)
        self.save_users(users)

        view = TeamView(self, ctx.author.id, ctx.author.display_name)
        embed, file = view.build_message()
        if file:
            message = await ctx.send(embed=embed, file=file, view=view)
        else:
            message = await ctx.send(embed=embed, view=view)
        view.message = message

    @commands.command()
    async def ranked(self, ctx):
        result, error = self.run_ranked_match(ctx.author.id)
        if error:
            await ctx.send(error)
            return

        await ctx.send(embed=self.ranked_result_embed(ctx, result))

    # Card info command to browse all bot cards with inventory-style filters.
    # EX: `.info -region LCK` lists LCK cards, `.info 1` opens full info for CID 1.
    @commands.command()
    async def info(self, ctx, *args):
        if args:
            first_arg = str(args[0]).lstrip("#")
            if first_arg.isdigit():
                card_entry = self.get_card_info_by_number(int(first_arg))
                if card_entry is None:
                    await ctx.send("That CID does not exist.")
                    return

                view = CardInfoView(
                    cog=self,
                    author_id=ctx.author.id,
                    cards=[card_entry],
                    rarity_counts=self.get_all_pulled_rarity_counts(),
                    filters=None
                )
                await ctx.send(embed=view.build_embed())
                return

        indexed_cards, rarity_counts, filters = self.get_filtered_card_info(args)

        view = CardInfoListView(
            cog=self,
            author_id=ctx.author.id,
            indexed_cards=indexed_cards,
            rarity_counts=rarity_counts,
            filters=filters
        )

        await ctx.send(embed=view.build_embed(), view=view)
    

    # View command to view a card  by its inventory number (the number shown in the inventory list)
    # EX: `.view 3` 
    @commands.command()
    async def view(self, ctx, inventory_number: int):
        owned_card, card_data, player, error = self.get_owned_card_by_inventory_number(
            ctx.author.id,
            inventory_number
        )

        if error:
            await ctx.send(error)
            return

        embed = self.card_embed(
            player=player,
            card_data=card_data,
            card_instance=owned_card,
            pulled_by_name=owned_card.get("pulled_by_username", ctx.author.name)
        )

        pulled_on = owned_card.get("pulled_on")
        if pulled_on:
            dt = datetime.fromisoformat(pulled_on)
            formatted = dt.strftime("%B %d, %Y")
            embed.add_field(name="Pulled on", value=formatted, inline=False)

        embed.set_footer(text=f"Pulled by {owned_card.get('pulled_by_username', ctx.author.name)}")

        await ctx.send(embed=embed)

    # Command to open a pack 
    # EX: `.open "LCK Spring 2026 Pack"` or `.open 1` to open the first pack in the list
    @commands.command()
    async def open(self, ctx, arg):
        users, user_data = self.get_user_data(ctx.author.id)
        if user_data is None:
            await ctx.send("You need to create a profile first with `.join`.")
            return

        user_packs = user_data.get("packs", [])

        if not user_packs:
            await ctx.send("You have no packs.")
            return

        # index case
        if arg.isdigit():
            index = int(arg)

            if index < 1 or index > len(user_packs):
                await ctx.send("Invalid pack number.")
                return

            pack_id = user_packs[index - 1]
            if pack_id is None:
                await ctx.send("That pack slot is empty.")
                return
            user_packs[index - 1] = None

        # name case
        else:
            arg_lower = arg.lower()
            pack_id = None
            for owned_pack_id in user_packs:
                if owned_pack_id is None:
                    continue
                pack = self.packs.get(owned_pack_id)
                pack_name = pack.get("name", "") if pack else ""
                if owned_pack_id.lower() == arg_lower or pack_name.lower() == arg_lower:
                    pack_id = owned_pack_id
                    break

            if pack_id is None:
                await ctx.send("You don't have that pack.")
                return

            for i, existing in enumerate(user_packs):
                if existing == pack_id:
                    user_packs[i] = None
                    break

        self.save_users(users)

        results, error = self.open_pack(ctx.author.id, pack_id, ctx.author)

        if error:
            await ctx.send(error)
            return

        lines = []
        for inst, card, player in results:
            lines.append(f"{player['name']} • {card['team']} • {inst['rarity']}")

        pack_name = self.packs.get(pack_id, {}).get("name", pack_id)
        embed = discord.Embed(
            title=f"{ctx.author.display_name} opened {pack_name}",
            description="\n".join(lines),
            color=discord.Color.dark_grey()
        )

        await ctx.send(embed=embed)
        

        #Command to list users packs
        # EX: `.packs`
    @commands.command()
    async def packs(self, ctx):
        users, user_data = self.get_user_data(ctx.author.id)
        if user_data is None:
            await ctx.send("You need to create a profile first with `.join`.")
            return

        user_packs = user_data.get("packs", [])

        if not any(p is not None for p in user_packs):
            await ctx.send("You have no packs.")
            return

        lines = []

        for i, pack_id in enumerate(user_packs, start=1):
            if pack_id is None:
                continue

            pack = self.packs.get(pack_id, {})
            display_name = pack.get("name", pack_id)
            lines.append(f"{i}. {display_name} ({pack_id})")

        embed = discord.Embed(
            title=f"{ctx.author.display_name}'s Packs",
            description="\n".join(lines),
            color=discord.Color.dark_grey()
        )

        await ctx.send(embed=embed)



async def setup(bot):
    await bot.add_cog(Cards(bot))
