import json
import random
import uuid
from datetime import datetime
import discord
from discord.ext import commands

PLAYERS_PATH = "data/players.json"
CARDS_PATH = "data/cards.json"
USERS_PATH = "data/users.json"
PACKS_PATH = "data/packs.json"

CARDS_PER_PAGE = 20


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
        return self.load_json(USERS_PATH, default={})

    def save_users(self, users_data):
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
            return {digits[-2:]}
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
            return "Immortal"
        return "Radiant"

    def create_card_instance(self, card_id, card_data=None):
        instance = {
            "instance_id": str(uuid.uuid4()),
            "card_id": card_id,
            "rarity": self.roll_rarity(),
            "pulled_on": datetime.utcnow().isoformat()
        }
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
        users[uid]["cards"].append(card_instance)
        self.save_users(users)

    def pull_random_card_for_user(self, user_id):
        users, user_data = self.get_user_data(user_id)

        if user_data is None:
            return None, None, None, "You need to create a profile first with `.join`."

        if not self.cards:
            return None, None, None, "No cards are loaded."

        chosen_card = random.choice(list(self.cards.values()))
        player = self.get_player_for_card(chosen_card)

        if player is None:
            return None, None, None, "Card data is missing a valid player."

        card_instance = self.create_card_instance(chosen_card["card_id"], chosen_card)
        self.add_card_to_user(users, user_id, card_instance)

        return card_instance, chosen_card, player, None

    # -----------------
    # Inventory helpers
    # -----------------

    def parse_inventory_filters(self, args):
        filters = {}
        valid_flags = {"-team", "-rarity", "-player", "-set", "-role"}

        i = 0
        while i < len(args):
            current = args[i].lower()

            if current in valid_flags:
                key = current[1:]
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
        
        if "role" in filters:
            if filters["role"].lower() not in player.get("role", "").lower():
                return False

        return True

    def filter_owned_cards(self, owned_cards, filters):
        if not filters:
            return list(enumerate(owned_cards, start=1))

        filtered = []

        for index, owned in enumerate(owned_cards, start=1):
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

        return f"{index}. {player_name} {set_name} {rarity}"

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

            lines.append(f"{index}. {player_name} {set_name} {rarity}")

        return lines

    def build_filter_text(self, filters):
        if not filters:
            return None
        return " • ".join(f"{key.capitalize()}: {value}" for key, value in filters.items())

    def get_filtered_inventory(self, user_id, args):
        users, user_data = self.get_user_data(user_id)

        if user_data is None:
            return None, None, "You need to create a profile first with `.join`."

        owned_cards = user_data.get("cards", [])
        if not owned_cards:
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

        if not owned_cards:
            return None, None, None, "Your inventory is empty."

        if inventory_number < 1 or inventory_number > len(owned_cards):
            return None, None, None, "That inventory number does not exist."

        owned_card = owned_cards[inventory_number - 1]
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
    # Embed helpers
    # -----------------

    def get_rarity_color(self, rarity):
        colors = {
            "Silver": discord.Color.light_grey(),
            "Gold": discord.Color.gold(),
            "Diamond": discord.Color.blue(),
            "Immortal": discord.Color.purple(),
            "Radiant": discord.Color.orange(),
        }
        return colors.get(rarity, discord.Color.default())

    def card_embed(self, player, card_data, card_instance, pulled_by_name):
        rarity = card_instance.get("rarity", "Unknown")

        embed = discord.Embed(
            title=player.get("name", "Unknown"),
            color=self.get_rarity_color(rarity)
        )

        embed.add_field(name="Team", value=card_data.get("team", "Unknown"), inline=False)
        embed.add_field(name="Role", value=player.get("role", "Unknown"), inline=False)
        embed.add_field(name="Set", value=card_data.get("set", "Unknown Set"), inline=False)
        embed.add_field(name="Rarity", value=rarity, inline=False)

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

    def open_pack(self, user_id, pack_name):
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

            card_instance = self.create_card_instance(chosen_card["card_id"], chosen_card)
            self.add_card_to_user(users, user_id, card_instance)

            results.append((card_instance, chosen_card, player))

        return results, None

    # -----------------
    # Commands
    # -----------------


    # Testing command to pull a card without cooldowns or costs
    @commands.command()
    async def pull(self, ctx):
        card_instance, card_data, player, error = self.pull_random_card_for_user(ctx.author.id)

        if error:
            await ctx.send(error)
            return

        embed = self.card_embed(
            player=player,
            card_data=card_data,
            card_instance=card_instance,
            pulled_by_name=ctx.author.display_name
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
            pulled_by_name=ctx.author.display_name
        )

        pulled_on = owned_card.get("pulled_on")
        if pulled_on:
            dt = datetime.fromisoformat(pulled_on)
            formatted = dt.strftime("%B %d, %Y")
            embed.add_field(name="Pulled on", value=formatted, inline=False)

        embed.set_footer(text=f"Pulled by {ctx.author.display_name}")

        await ctx.send(embed=embed)

    # Temporary command to get a random pack for testing (or many)
    # EX: .getpack 3
    @commands.command()
    async def getpack(self, ctx, amount: int = 1):
        if not self.packs:
            await ctx.send("No packs available.")
            return

        users, user_data = self.get_user_data(ctx.author.id)

        if user_data is None:
            await ctx.send("You need to create a profile first with `.join`.")
            return

        packs = user_data.get("packs", [])
        if not isinstance(packs, list):
            packs = []
        user_data["packs"] = packs

        pack_ids = list(self.packs.keys())

        for _ in range(amount):
            chosen = random.choice(pack_ids)
            packs.append(chosen)

        self.save_users(users)

        await ctx.send(f"Gave you {amount} random pack(s).")

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

            pack_id = user_packs.pop(index - 1)

        # name case
        else:
            arg_lower = arg.lower()
            pack_id = None
            for owned_pack_id in user_packs:
                pack = self.packs.get(owned_pack_id)
                pack_name = pack.get("name", "") if pack else ""
                if owned_pack_id.lower() == arg_lower or pack_name.lower() == arg_lower:
                    pack_id = owned_pack_id
                    break

            if pack_id is None:
                await ctx.send("You don't have that pack.")
                return

            user_packs.remove(pack_id)

        self.save_users(users)

        results, error = self.open_pack(ctx.author.id, pack_id)

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

        if not user_packs:
            await ctx.send("You have no packs.")
            return

        lines = []

        for i, pack_id in enumerate(user_packs, start=1):
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