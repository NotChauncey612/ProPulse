import json
import uuid
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

AUCTIONS_PATH = "data/auctions.json"
HISTORY_PATH = "data/auctions_history.json"


class Auction(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_auctions.start()

    # -----------------
    # JSON
    # -----------------

    def load_auctions(self):
        try:
            with open(AUCTIONS_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_auctions(self, auctions):
        with open(AUCTIONS_PATH, "w", encoding="utf-8") as f:
            json.dump(auctions, f, indent=4)

    def load_history(self):
        try:
            with open(HISTORY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except Exception:
            return []

    def save_history(self, history):
        with open(HISTORY_PATH, "w", encoding="utf-8") as f:
            json.dump(history, f, indent=4)

    # -----------------
    # Helpers
    # -----------------

    def get_time_remaining(self, expires_at):
        now = datetime.utcnow()
        end = datetime.fromisoformat(expires_at)

        if now >= end:
            return "Expired"

        remaining = end - now
        hours, remainder = divmod(int(remaining.total_seconds()), 3600)
        minutes, _ = divmod(remainder, 60)

        return f"{hours}h {minutes}m"

    # -----------------
    # Create Auction
    # -----------------

    def create_auction(self, seller_id, item_type, item_data, start_price, buy_now):
        auctions = self.load_auctions()

        auction = {
            "auction_id": str(uuid.uuid4()),
            "seller_id": str(seller_id),
            "item_type": item_type,
            "card_instance": item_data if item_type == "card" else None,
            "pack_name": item_data if item_type == "pack" else None,
            "starting_price": start_price,
            "buy_now_price": buy_now,
            "current_bid": start_price,
            "highest_bidder": None,
            "created_at": datetime.utcnow().isoformat(),
            "expires_at": (datetime.utcnow() + timedelta(hours=24)).isoformat()
        }

        auctions.append(auction)
        self.save_auctions(auctions)

    # -----------------
    # Bidding
    # -----------------

    def place_bid(self, auction_id, bidder_id, amount):
        auctions = self.load_auctions()
        users_cog = self.bot.get_cog("Users")

        min_increment = 10

        for auction in auctions:
            if auction["auction_id"] != auction_id:
                continue

            if datetime.utcnow() >= datetime.fromisoformat(auction["expires_at"]):
                return "This auction has expired."

            if str(bidder_id) == auction["seller_id"]:
                return "You cannot bid on your own auction."

            current_bid = auction["current_bid"]
            highest_bidder = auction["highest_bidder"]

            if amount < current_bid + min_increment:
                return f"Minimum bid is {current_bid + min_increment}."

            profile = users_cog.get_profile_by_id(str(bidder_id))

            if highest_bidder == str(bidder_id):
                diff = amount - current_bid
                if profile["gold"] < diff:
                    return "You don't have enough gold."
                profile["gold"] -= diff
            else:
                if profile["gold"] < amount:
                    return "You don't have enough gold."

                if highest_bidder:
                    prev = users_cog.get_profile_by_id(highest_bidder)
                    prev["gold"] += current_bid

                profile["gold"] -= amount

            auction["current_bid"] = amount
            auction["highest_bidder"] = str(bidder_id)

            users_cog.save_users()
            self.save_auctions(auctions)
            return None

        return "Auction not found."

    # -----------------
    # Filters
    # -----------------

    def parse_filters(self, args):
        filters = {}
        valid = {"-team", "-rarity", "-player", "-set"}

        i = 0
        while i < len(args):
            if args[i] in valid:
                key = args[i][1:]
                i += 1
                value = []

                while i < len(args) and args[i] not in valid:
                    value.append(args[i])
                    i += 1

                if value:
                    filters[key] = " ".join(value)
            else:
                i += 1

        return filters

    def auction_matches(self, auction, filters, cards_cog):
        if auction["item_type"] != "card":
            return True

        card = auction["card_instance"]
        card_data = cards_cog.get_card_by_id(card.get("card_id"))
        if not card_data and card.get("snapshot"):
            card_data = card["snapshot"]
        if not card_data:
            return False

        player = cards_cog.get_player_for_card(card_data)
        if not player:
            return False

        if "team" in filters and card_data.get("team", "").lower() != filters["team"].lower():
            return False
        if "rarity" in filters and card.get("rarity", "").lower() != filters["rarity"].lower():
            return False
        if "player" in filters and player.get("name", "").lower() != filters["player"].lower():
            return False
        if "set" in filters and card_data.get("set", "").lower() != filters["set"].lower():
            return False

        return True

    # -----------------
    # Resolve Auctions
    # -----------------

    @tasks.loop(minutes=1)
    async def check_auctions(self):
        auctions = self.load_auctions()
        now = datetime.utcnow()

        remaining = []

        for auc in auctions:
            if datetime.fromisoformat(auc["expires_at"]) <= now:
                self.resolve_auction(auc)
            else:
                remaining.append(auc)

        self.save_auctions(remaining)

    def resolve_auction(self, auction):
        users_cog = self.bot.get_cog("Users")
        cards_cog = self.bot.get_cog("Cards")

        history = self.load_history()

        winner = auction["highest_bidder"]
        sold = winner is not None
        final_owner = winner if sold else auction["seller_id"]

        if auction["item_type"] == "card":
            users, user_data = cards_cog.get_user_data(final_owner)
            if user_data is not None:
                user_data.setdefault("cards", [])
                user_data["cards"].append(auction["card_instance"])
                cards_cog.save_users(users)
        else:
            profile = users_cog.get_profile_by_id(final_owner)
            profile.setdefault("packs", [])
            profile["packs"].append(auction["pack_name"])
            users_cog.save_users()

        if sold:
            seller = users_cog.get_profile_by_id(auction["seller_id"])
            seller["gold"] += auction["current_bid"]
            users_cog.save_users()

        history.append({
            "auction_id": auction["auction_id"],
            "item_type": auction["item_type"],
            "card_instance": auction["card_instance"],
            "pack_name": auction["pack_name"],
            "seller_id": auction["seller_id"],
            "winner_id": winner,
            "final_price": auction["current_bid"] if sold else None,
            "sold": sold,
            "ended_at": datetime.utcnow().isoformat()
        })

        self.save_history(history)

    # -----------------
    # Command
    # -----------------

    @commands.command()
    async def auction(self, ctx, *args):
        cards_cog = self.bot.get_cog("Cards")
        users_cog = self.bot.get_cog("Users")

        if args and args[0] == "-sell":
            index = int(args[1])
            owned_card, _, _, error = cards_cog.get_owned_card_by_inventory_number(ctx.author.id, index)

            if error:
                await ctx.send(error)
                return

            await ctx.send(
                "Click below to create your auction:",
                view=SellView(self, ctx.author.id, owned_card, "card")
            )
            return

        if args and args[0] == "-sellpack":
            index = int(args[1])
            profile = users_cog.get_profile(ctx.author)
            packs = profile.get("packs", [])

            if not packs or index < 1 or index > len(packs):
                await ctx.send("Invalid pack index.")
                return

            pack_name = packs[index - 1]
            await ctx.send("Fill this out to create your pack auction:", view=SellPackView(self, ctx.author.id, pack_name))
            return

        auctions = self.load_auctions()
        filters = self.parse_filters(args)
        filtered = [a for a in auctions if self.auction_matches(a, filters, cards_cog)]

        view = AuctionView(self, ctx.author.id, filtered)
        await ctx.send(embed=view.build_embed(), view=view)


class SellView(discord.ui.View):
    def __init__(self, cog, user_id, item, item_type):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.item = item
        self.item_type = item_type

    @discord.ui.button(label="Create Auction", style=discord.ButtonStyle.green)
    async def open_modal(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(
            SellModal(self.cog, self.user_id, self.item, self.item_type)
        )


class SellPackView(discord.ui.View):
    def __init__(self, cog, user_id, pack_name):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.pack_name = pack_name

    @discord.ui.button(label="Create Pack Auction", style=discord.ButtonStyle.green)
    async def open_modal(self, interaction: discord.Interaction, button):
        await interaction.response.send_modal(
            SellModal(self.cog, self.user_id, self.pack_name, "pack")
        )


class SellModal(discord.ui.Modal, title="Create Auction"):
    starting_price = discord.ui.TextInput(label="Starting Price")
    buy_now_price = discord.ui.TextInput(label="Buy Now Price", required=False)

    def __init__(self, cog, user_id, item, item_type):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.item = item
        self.item_type = item_type

    async def on_submit(self, interaction: discord.Interaction):
        start = int(self.starting_price.value)
        buy = int(self.buy_now_price.value) if self.buy_now_price.value else None

        users_cog = interaction.client.get_cog("Users")
        cards_cog = interaction.client.get_cog("Cards")

        if self.item_type == "card":
            users, user_data = cards_cog.get_user_data(self.user_id)
            user_data["cards"].remove(self.item)
            cards_cog.save_users(users)
        else:
            profile = users_cog.get_profile_by_id(str(self.user_id))
            profile["packs"].remove(self.item)
            users_cog.save_users()

        self.cog.create_auction(self.user_id, self.item_type, self.item, start, buy)
        await interaction.response.send_message("✅ Auction created!", ephemeral=True)


class BidModal(discord.ui.Modal, title="Place Bid"):
    bid_amount = discord.ui.TextInput(label="Bid Amount")

    def __init__(self, cog, auction):
        super().__init__()
        self.cog = cog
        self.auction = auction

    async def on_submit(self, interaction: discord.Interaction):
        amount = int(self.bid_amount.value)
        error = self.cog.place_bid(self.auction["auction_id"], interaction.user.id, amount)

        if error:
            await interaction.response.send_message(error, ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Bid placed: {amount}", ephemeral=True)


class BuyView(discord.ui.View):
    def __init__(self, auction, cog):
        super().__init__()
        self.auction = auction
        self.cog = cog

    @discord.ui.button(label="Bid", style=discord.ButtonStyle.blurple)
    async def bid(self, interaction, button):
        await interaction.response.send_modal(BidModal(self.cog, self.auction))

    @discord.ui.button(label="Buy Now", style=discord.ButtonStyle.green)
    async def buy(self, interaction, button):
        users_cog = interaction.client.get_cog("Users")
        cards_cog = interaction.client.get_cog("Cards")

        price = self.auction["buy_now_price"]
        if not price:
            await interaction.response.send_message("No buy now price.", ephemeral=True)
            return

        profile = users_cog.get_profile(interaction.user)
        if profile["gold"] < price:
            await interaction.response.send_message("Not enough gold.", ephemeral=True)
            return

        profile["gold"] -= price

        if self.auction["item_type"] == "card":
            users, user_data = cards_cog.get_user_data(interaction.user.id)
            user_data["cards"].append(self.auction["card_instance"])
            cards_cog.save_users(users)
        else:
            profile.setdefault("packs", [])
            profile["packs"].append(self.auction["pack_name"])

        users_cog.save_users()
        await interaction.response.send_message("✅ Purchased!", ephemeral=True)


class AuctionSelect(discord.ui.Select):
    def __init__(self, auctions, cog):
        self.auctions = auctions
        self.cog = cog

        options = [
            discord.SelectOption(
                label=f"{i + 1}. {a['current_bid']} gold",
                value=str(i)
            )
            for i, a in enumerate(auctions)
        ]

        if not options:
            options = [discord.SelectOption(label="No auctions", value="0")]

        super().__init__(placeholder="Select auction", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if not self.auctions:
            await interaction.response.send_message("No auctions available.", ephemeral=True)
            return
        auction = self.auctions[int(self.values[0])]
        await interaction.response.send_message(
            "Choose an action:",
            view=BuyView(auction, self.cog),
            ephemeral=True
        )


class AuctionView(discord.ui.View):
    def __init__(self, cog, user_id, auctions):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.auctions = auctions
        self.page = 0

        self.update_buttons()
        self.add_item(AuctionSelect(self.get_page_items(), cog))

    def get_page_items(self):
        start = self.page * 5
        return self.auctions[start:start + 5]

    def total_pages(self):
        return max(1, (len(self.auctions) - 1) // 5 + 1)

    def build_embed(self):
        cards_cog = self.cog.bot.get_cog("Cards")
        embed = discord.Embed(title="Auction House")

        if not self.auctions:
            embed.description = "No auctions available."
            return embed

        for i, auc in enumerate(self.get_page_items(), start=1):
            if auc["item_type"] == "card":
                card = auc["card_instance"]
                card_data = cards_cog.get_card_by_id(card.get("card_id"))
                if not card_data and card.get("snapshot"):
                    card_data = card["snapshot"]

                if card_data:
                    player = cards_cog.get_player_for_card(card_data)
                    name = player.get("name", "Unknown")
                    details = f"{card_data.get('team', 'Unknown')} • {card.get('rarity', 'Unknown')}"
                else:
                    name = "Unknown Card"
                    details = card.get("rarity", "Unknown")
            else:
                name = str(auc["pack_name"]).replace("_", " ").title()
                details = "Pack"

            embed.add_field(
                name=f"{i}. {name}",
                value=(
                    f"{details}\n"
                    f"Bid: {auc['current_bid']}\n"
                    f"Buy Now: {auc['buy_now_price']}\n"
                    f"Time: {self.cog.get_time_remaining(auc['expires_at'])}"
                ),
                inline=False
            )

        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages()}")
        return embed

    def update_buttons(self):
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.total_pages() - 1

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction, button):
        self.page -= 1
        self.update_buttons()
        self.clear_items()
        self.add_item(AuctionSelect(self.get_page_items(), self.cog))
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary)
    async def next(self, interaction, button):
        self.page += 1
        self.update_buttons()
        self.clear_items()
        self.add_item(AuctionSelect(self.get_page_items(), self.cog))
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.green)
    async def refresh(self, interaction, button):
        self.auctions = self.cog.load_auctions()
        self.clear_items()
        self.add_item(AuctionSelect(self.get_page_items(), self.cog))
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


async def setup(bot):
    await bot.add_cog(Auction(bot))