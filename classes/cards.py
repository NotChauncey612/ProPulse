import json
import random
import uuid
from datetime import datetime

import discord
from discord.ext import commands

PLAYERS_PATH = "data/players.json"
CARDS_PATH = "data/cards.json"
USERS_PATH = "data/users.json"


class Cards(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.players = self.load_json(PLAYERS_PATH)
        self.cards = self.load_json(CARDS_PATH)

    def load_json(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except FileNotFoundError:
            return [] if "players" in path or "cards" in path else {}

    def save_users(self, users_data):
        with open(USERS_PATH, "w", encoding="utf-8") as f:
            json.dump(users_data, f, indent=4)

    def get_player_by_id(self, player_id):
        for player in self.players:
            if player["id"] == player_id:
                return player
        return None

    def get_rarity_color(self, rarity):
        colors = {
            "Silver": discord.Color.light_grey(),
            "Gold": discord.Color.gold(),
            "Diamond": discord.Color.blue(),
            "Immortal": discord.Color.purple(),
            "Radiant": discord.Color.orange()
        }
        return colors.get(rarity, discord.Color.default())

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

    def generate_card_instance(self, card_id):
        return {
            "instance_id": str(uuid.uuid4()),
            "card_id": card_id,
            "rarity": self.roll_rarity(),
            "pulled_at": datetime.utcnow().isoformat()
        }
    
    def get_card_by_id(self, card_id):
        for card in self.cards:
            if card["id"] == card_id:
                return card
        return None
    
    def parse_inventory_filters(self, args):
        filters = {}
        valid_flags = {"-team", "-rarity", "-player", "-set"}

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


    def filter_owned_cards(self, owned_cards, filters):
        filtered = []

        for owned in owned_cards:
            card_data = self.get_card_by_id(owned["card_id"])
            if not card_data:
                continue

            player = self.get_player_by_id(card_data["player_id"])
            if not player:
                continue

            matches = True

            if "team" in filters:
                if card_data.get("team", "").lower() != filters["team"].lower():
                    matches = False

            if "rarity" in filters:
                if owned.get("rarity", "").lower() != filters["rarity"].lower():
                    matches = False

            if "player" in filters:
                if player.get("name", "").lower() != filters["player"].lower():
                    matches = False

            if "set" in filters:
                if card_data.get("set", "").lower() != filters["set"].lower():
                    matches = False

            if matches:
                filtered.append(owned)

        return filtered
    
    # Commands

    # Inventory command to show user's cards
    @commands.command(aliases=["inv"])
    async def inventory(self, ctx, *args):
        users = self.load_json(USERS_PATH)
        uid = str(ctx.author.id)

        if uid not in users:
            await ctx.send("You need to create a profile first with `.join`.")
            return

        owned_cards = users[uid].get("cards", [])

        if not owned_cards:
            await ctx.send("Your inventory is empty.")
            return

        filters = self.parse_inventory_filters(args)
        filtered_cards = self.filter_owned_cards(owned_cards, filters)

        if not filtered_cards:
            await ctx.send("No cards matched those filters.")
            return

        view = InventoryView(self, ctx.author.id, filtered_cards)
        view.update_buttons()
        embed = view.build_embed(ctx.author.display_name)

        if filters:
            filter_text = " • ".join(f"{k.capitalize()}: {v}" for k, v in filters.items())
            embed.title = f"{ctx.author.display_name}'s Inventory"
            embed.description = f"Filters: {filter_text}\n\n{embed.description}"

        await ctx.send(embed=embed, view=view)

    # Temporary command to test pulling a card
    @commands.command()
    async def pull(self, ctx):
        users = self.load_json(USERS_PATH)
        uid = str(ctx.author.id)

        if uid not in users:
            await ctx.send("You need to create a profile first with `.join`.")
            return

        if "cards" not in users[uid]:
            users[uid]["cards"] = []

        if not self.cards:
            await ctx.send("No cards are loaded.")
            return

        chosen_card = random.choice(self.cards)
        player = self.get_player_by_id(chosen_card["player_id"])

        if player is None:
            await ctx.send("Card data is missing a valid player.")
            return

        card_instance = self.generate_card_instance(chosen_card["id"])
        users[uid]["cards"].append(card_instance)
        self.save_users(users)

        rarity = card_instance["rarity"]

        embed = discord.Embed(
            title=player["name"],
            color=self.get_rarity_color(rarity)
        )

        embed.add_field(name="Team", value=chosen_card["team"], inline=False)
        embed.add_field(name="Role", value=player["role"], inline=False)
        embed.add_field(name="Set", value=f"{chosen_card['set']} {chosen_card['year']}", inline=False)
        embed.add_field(name="Rarity", value=rarity, inline=False
        )

        image_url = chosen_card.get("image_url", "")
        if image_url:
            embed.set_image(url=image_url)

        embed.set_footer(text=f"Pulled by {ctx.author.display_name}")

        await ctx.send(embed=embed)


async def setup(bot):
    await bot.add_cog(Cards(bot))