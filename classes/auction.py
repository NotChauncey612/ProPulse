import uuid
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

from .storage import load_json, save_json

AUCTIONS_PATH = "data/auctions.json"
HISTORY_PATH = "data/auctions_history.json"
DEFAULT_AUCTION_DAYS = 1
MAX_AUCTION_DAYS = 7
AUCTIONS_PER_PAGE = 12
MAX_ACTIVE_AUCTIONS_PER_USER = 12
CASH_EMOJI = "💵"


class Auction(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.check_auctions.start()

    # -----------------
    # JSON
    # -----------------

    def load_auctions(self):
        data = load_json(AUCTIONS_PATH, default=[])
        return data if isinstance(data, list) else []

    def save_auctions(self, auctions):
        save_json(AUCTIONS_PATH, auctions)

    def get_active_auction_count(self, seller_id):
        seller_id = str(seller_id)
        return sum(1 for auction in self.load_auctions() if auction.get("seller_id") == seller_id)

    def has_auction_room(self, seller_id):
        return self.get_active_auction_count(seller_id) < MAX_ACTIVE_AUCTIONS_PER_USER

    def load_history(self):
        data = load_json(HISTORY_PATH, default=[])
        return data if isinstance(data, list) else []

    def save_history(self, history):
        save_json(HISTORY_PATH, history)

    # -----------------
    # Helpers
    # -----------------

    def get_time_remaining(self, expires_at):
        now = datetime.utcnow()
        end = datetime.fromisoformat(expires_at)

        if now >= end:
            return "Expired"

        total_seconds = max(0, int((end - now).total_seconds()))
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"

        return f"{minutes}m {seconds}s"

    # -----------------
    # Create Auction
    # -----------------

    def create_auction(self, seller_id, item_type, item_data, start_price, buy_now, duration_days=DEFAULT_AUCTION_DAYS):
        auctions = self.load_auctions()
        now = datetime.utcnow()

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
            "created_at": now.isoformat(),
            "duration_days": duration_days,
            "expires_at": (now + timedelta(days=duration_days)).isoformat()
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

        if amount <= 0:
            return "Bid must be greater than zero."

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
                return f"Minimum bid is {CASH_EMOJI} {current_bid + min_increment} cash."

            profile = users_cog.get_profile_by_id(str(bidder_id))

            if highest_bidder == str(bidder_id):
                diff = amount - current_bid
                if profile["cash"] < diff:
                    return "You don't have enough cash."
                profile["cash"] -= diff
            else:
                if profile["cash"] < amount:
                    return "You don't have enough cash."

                if highest_bidder:
                    prev = users_cog.get_profile_by_id(highest_bidder)
                    prev["cash"] += current_bid

                profile["cash"] -= amount

            auction["current_bid"] = amount
            auction["highest_bidder"] = str(bidder_id)

            users_cog.save_users()
            self.save_auctions(auctions)
            return None

        return "Auction not found."

    def complete_buy_now(self, auction_id, buyer_id):
        auctions = self.load_auctions()
        users_cog = self.bot.get_cog("Users")
        cards_cog = self.bot.get_cog("Cards")

        target = None
        remaining = []
        for auc in auctions:
            if auc["auction_id"] == auction_id:
                target = auc
            else:
                remaining.append(auc)

        if target is None:
            return None, "Auction not found."

        if str(buyer_id) == target["seller_id"]:
            return None, "You cannot buy your own auction. Use Take Down if nobody has bid yet."

        price = target.get("buy_now_price")
        if not price:
            return None, "No buy now price."

        buyer_profile = users_cog.get_profile_by_id(str(buyer_id))
        seller_profile = users_cog.get_profile_by_id(target["seller_id"])
        highest_bidder = target.get("highest_bidder")
        current_bid = target.get("current_bid", 0)
        held_by_buyer = current_bid if highest_bidder == str(buyer_id) else 0
        if buyer_profile["cash"] + held_by_buyer < price:
            return None, "Not enough cash."

        if highest_bidder and highest_bidder != str(buyer_id):
            previous_bidder = users_cog.get_profile_by_id(highest_bidder)
            previous_bidder["cash"] += current_bid
        elif highest_bidder == str(buyer_id):
            buyer_profile["cash"] += current_bid

        buyer_profile["cash"] -= price
        seller_profile["cash"] += price

        item_name = "Unknown"
        if target["item_type"] == "card":
            users, user_data = cards_cog.get_user_data(buyer_id)
            user_data.setdefault("cards", [])
            cards_cog.add_card_to_user(users, buyer_id, target["card_instance"])
            card_data = cards_cog.get_card_by_id(target["card_instance"].get("card_id")) or target["card_instance"].get("snapshot", {})
            item_name = cards_cog.get_player_for_card(card_data).get("name", "Unknown Card")
        else:
            users_cog.add_pack_to_first_slot(buyer_profile, target["pack_name"])
            item_name = str(target["pack_name"]).replace("_", " ").title()

        users_cog.save_users()
        self.save_auctions(remaining)
        history = self.load_history()
        history.append({
            "auction_id": target["auction_id"],
            "item_type": target["item_type"],
            "card_instance": target["card_instance"],
            "pack_name": target["pack_name"],
            "seller_id": target["seller_id"],
            "winner_id": str(buyer_id),
            "final_price": price,
            "sold": True,
            "ended_at": datetime.utcnow().isoformat()
        })
        self.save_history(history)
        return item_name, None

    def cancel_auction(self, auction_id, seller_id):
        auctions = self.load_auctions()
        users_cog = self.bot.get_cog("Users")
        cards_cog = self.bot.get_cog("Cards")

        target = None
        remaining = []
        for auction in auctions:
            if auction["auction_id"] == auction_id:
                target = auction
            else:
                remaining.append(auction)

        if target is None:
            return None, "Auction not found."

        if target.get("seller_id") != str(seller_id):
            return None, "You can only take down your own auctions."

        if target.get("highest_bidder"):
            return None, "You cannot take down an auction after someone has bid on it."

        item_name = "Unknown"
        if target["item_type"] == "card":
            users, user_data = cards_cog.get_user_data(seller_id)
            if user_data is None:
                return None, "Seller profile not found."
            user_data.setdefault("cards", [])
            cards_cog.add_card_to_user(users, seller_id, target["card_instance"])
            card_data = cards_cog.get_card_by_id(target["card_instance"].get("card_id")) or target["card_instance"].get("snapshot", {})
            item_name = cards_cog.get_player_for_card(card_data).get("name", "Unknown Card")
        else:
            profile = users_cog.get_profile_by_id(str(seller_id))
            if profile is None:
                return None, "Seller profile not found."
            users_cog.add_pack_to_first_slot(profile, target["pack_name"])
            users_cog.save_users()
            item_name = str(target["pack_name"]).replace("_", " ").title()

        self.save_auctions(remaining)
        return item_name, None

    # -----------------
    # Filters
    # -----------------

    def parse_filters(self, args):
        filters = {}
        sort = None
        valid = {"-team", "-rarity", "-player", "-set", "-role", "-league"}
        sort_flags = {"-sort", "-orderby", "-order"}
        sort_aliases = {
            "-lowestbid": "bid_asc",
            "-highestbid": "bid_desc",
            "-lowestbuy": "buy_asc",
            "-highestbuy": "buy_desc",
        }

        i = 0
        while i < len(args):
            current = args[i].lower()

            if current in sort_aliases:
                sort = sort_aliases[current]
                i += 1
            elif current in sort_flags:
                i += 1
                value = []

                while (
                    i < len(args)
                    and args[i].lower() not in valid
                    and args[i].lower() not in sort_flags
                    and args[i].lower() not in sort_aliases
                ):
                    value.append(args[i])
                    i += 1

                if value:
                    sort = self.normalize_sort_value(" ".join(value))
            elif current in valid:
                key = current[1:]
                i += 1
                value = []

                while (
                    i < len(args)
                    and args[i].lower() not in valid
                    and args[i].lower() not in sort_flags
                    and args[i].lower() not in sort_aliases
                ):
                    value.append(args[i])
                    i += 1

                if value:
                    filters[key] = " ".join(value)
            else:
                i += 1

        return filters, sort

    def normalize_sort_value(self, value):
        normalized = value.lower().replace("-", " ").replace("_", " ").strip()
        sort_values = {
            "lowest bid": "bid_asc",
            "low bid": "bid_asc",
            "bid asc": "bid_asc",
            "highest bid": "bid_desc",
            "high bid": "bid_desc",
            "bid desc": "bid_desc",
            "lowest buy now": "buy_asc",
            "lowest buy": "buy_asc",
            "buy asc": "buy_asc",
            "highest buy now": "buy_desc",
            "highest buy": "buy_desc",
            "buy desc": "buy_desc",
            "lowestbid": "bid_asc",
            "highestbid": "bid_desc",
            "lowestbuy": "buy_asc",
            "highestbuy": "buy_desc",
            "lowest buynow": "buy_asc",
            "highest buynow": "buy_desc",
            "player": "player",
            "team": "team",
            "rarity": "rarity",
            "set": "set",
            "league": "league",
            "role": "role",
            "time": "time",
        }
        return sort_values.get(normalized)

    def get_auction_card_context(self, auction, cards_cog):
        if auction["item_type"] != "card":
            return None, None, None

        card = auction["card_instance"]
        card_data = cards_cog.get_card_by_id(card.get("card_id"))
        if not card_data and card.get("snapshot"):
            card_data = card["snapshot"]
        if not card_data:
            return card, None, None

        return card, card_data, cards_cog.get_player_for_card(card_data)

    def auction_matches(self, auction, filters, cards_cog):
        if auction["item_type"] != "card":
            return not filters

        card, card_data, player = self.get_auction_card_context(auction, cards_cog)
        if not card_data:
            return False

        if not player:
            return False

        if "team" in filters and card_data.get("team", "").lower() != filters["team"].lower():
            return False
        if "rarity" in filters and card.get("rarity", "").lower() != filters["rarity"].lower():
            return False
        if "player" in filters:
            target = filters["player"].lower()
            player_name = player.get("name", "").lower()
            player_id = player.get("id", "").lower()
            if player_name != target and player_id != target:
                return False
        if "set" in filters and card_data.get("set", "").lower() != filters["set"].lower():
            return False
        if "league" in filters and card_data.get("league", "").lower() != filters["league"].lower():
            return False
        if "role" in filters and filters["role"].lower() not in player.get("role", "").lower():
            return False

        return True

    def sort_auctions(self, auctions, sort, cards_cog):
        if not sort:
            return auctions

        def buy_now_value(auction):
            return auction.get("buy_now_price") if auction.get("buy_now_price") is not None else float("inf")

        def card_value(auction, field):
            card, card_data, player = self.get_auction_card_context(auction, cards_cog)
            if field == "rarity":
                return (card or {}).get("rarity", "")
            if field == "player":
                return (player or {}).get("name", "")
            if field == "role":
                return (player or {}).get("role", "")
            if field in {"team", "set", "league"}:
                return (card_data or {}).get(field, "")
            return ""

        sorters = {
            "bid_asc": lambda auction: auction.get("current_bid", 0),
            "bid_desc": lambda auction: auction.get("current_bid", 0),
            "buy_asc": buy_now_value,
            "buy_desc": lambda auction: auction.get("buy_now_price") if auction.get("buy_now_price") is not None else -1,
            "time": lambda auction: auction.get("expires_at", ""),
            "rarity": lambda auction: card_value(auction, "rarity").lower(),
            "player": lambda auction: card_value(auction, "player").lower(),
            "team": lambda auction: card_value(auction, "team").lower(),
            "set": lambda auction: card_value(auction, "set").lower(),
            "league": lambda auction: card_value(auction, "league").lower(),
            "role": lambda auction: card_value(auction, "role").lower(),
        }

        reverse = sort in {"bid_desc", "buy_desc"}
        return sorted(auctions, key=sorters.get(sort, sorters["time"]), reverse=reverse)

    def filter_and_sort_auctions(self, auctions, filters, sort, cards_cog):
        filtered = [a for a in auctions if self.auction_matches(a, filters, cards_cog)]
        return self.sort_auctions(filtered, sort, cards_cog)

    def get_filter_options(self, auctions, filter_key, cards_cog):
        values = {}

        for auction in auctions:
            if auction["item_type"] != "card":
                continue

            card, card_data, player = self.get_auction_card_context(auction, cards_cog)
            if not card_data or not player:
                continue

            if filter_key == "player":
                value = player.get("name", "")
            elif filter_key == "rarity":
                value = (card or {}).get("rarity", "")
            elif filter_key == "role":
                value = player.get("role", "")
            elif filter_key in {"team", "set", "league"}:
                value = card_data.get(filter_key, "")
            else:
                value = ""

            value = str(value).strip()
            if value:
                values[value.lower()] = value

        return sorted(values.values(), key=str.lower)[:25]

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
                await self.resolve_auction(auc)
            else:
                remaining.append(auc)

        self.save_auctions(remaining)

    async def resolve_auction(self, auction):
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
            users_cog.add_pack_to_first_slot(profile, auction["pack_name"])
            users_cog.save_users()

        if sold:
            seller = users_cog.get_profile_by_id(auction["seller_id"])
            seller["cash"] += auction["current_bid"]
            users_cog.save_users()

            winner_user = self.bot.get_user(int(winner)) if winner else None
            seller_user = self.bot.get_user(int(auction["seller_id"]))
            if winner_user and users_cog.get_profile_by_id(winner).get("settings", {}).get("dm_auction_notis", True):
                try:
                    await winner_user.send(f"🏆 You won auction `{auction['auction_id']}` for {CASH_EMOJI} {auction['current_bid']} cash.")
                except Exception:
                    pass
            if seller_user and users_cog.get_profile_by_id(auction["seller_id"]).get("settings", {}).get("dm_auction_notis", True):
                try:
                    await seller_user.send(f"{CASH_EMOJI} Your auction `{auction['auction_id']}` sold for {auction['current_bid']} cash.")
                except Exception:
                    pass
        else:
            seller_user = self.bot.get_user(int(auction["seller_id"]))
            if seller_user and users_cog.get_profile_by_id(auction["seller_id"]).get("settings", {}).get("dm_auction_notis", True):
                try:
                    await seller_user.send(f"📦 Your auction `{auction['auction_id']}` ended with no bids. Item returned.")
                except Exception:
                    pass

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
            if len(args) < 2 or not str(args[1]).isdigit():
                await ctx.send("Use `.auction -sell <inventory #>`.")
                return
            index = int(args[1])
            if not self.has_auction_room(ctx.author.id):
                await ctx.send(f"You can only have {MAX_ACTIVE_AUCTIONS_PER_USER} active auctions at once.")
                return

            owned_card, _, _, error = cards_cog.get_owned_card_by_inventory_number(ctx.author.id, index)

            if error:
                await ctx.send(error)
                return
            if cards_cog.is_card_in_user_team(ctx.author.id, owned_card):
                await ctx.send("That card is in your team. Remove or replace it before auctioning it.")
                return

            await ctx.send(
                "Click below to create your auction:",
                view=SellView(self, ctx.author.id, owned_card, "card")
            )
            return

        if args and args[0] == "-sellpack":
            if len(args) < 2 or not str(args[1]).isdigit():
                await ctx.send("Use `.auction -sellpack <pack #>`.")
                return
            index = int(args[1])
            if not self.has_auction_room(ctx.author.id):
                await ctx.send(f"You can only have {MAX_ACTIVE_AUCTIONS_PER_USER} active auctions at once.")
                return

            profile = users_cog.get_profile(ctx.author)
            packs = profile.get("packs", [])

            if not packs or index < 1 or index > len(packs):
                await ctx.send("Invalid pack index.")
                return

            pack_name = packs[index - 1]
            if pack_name is None:
                await ctx.send("That pack slot is empty.")
                return

            await ctx.send("Fill this out to create your pack auction:", view=SellPackView(self, ctx.author.id, index - 1, pack_name))
            return

        auctions = self.load_auctions()
        filters, sort = self.parse_filters(args)
        filtered = self.filter_and_sort_auctions(auctions, filters, sort, cards_cog)

        view = AuctionView(self, ctx.author.id, filtered, filters, sort)
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
        if not self.cog.has_auction_room(self.user_id):
            await interaction.response.send_message(
                f"You can only have {MAX_ACTIVE_AUCTIONS_PER_USER} active auctions at once.",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(
            SellModal(self.cog, self.user_id, self.item, self.item_type)
        )


class SellPackView(discord.ui.View):
    def __init__(self, cog, user_id, pack_slot, pack_name):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.pack_slot = pack_slot
        self.pack_name = pack_name

    @discord.ui.button(label="Create Pack Auction", style=discord.ButtonStyle.green)
    async def open_modal(self, interaction: discord.Interaction, button):
        if not self.cog.has_auction_room(self.user_id):
            await interaction.response.send_message(
                f"You can only have {MAX_ACTIVE_AUCTIONS_PER_USER} active auctions at once.",
                ephemeral=True
            )
            return
        await interaction.response.send_modal(
            SellModal(self.cog, self.user_id, self.pack_name, "pack", self.pack_slot)
        )


class SellModal(discord.ui.Modal, title="Create Auction"):
    starting_price = discord.ui.TextInput(label="Starting Cash Price")
    buy_now_price = discord.ui.TextInput(label="Buy Now Cash Price", required=False)
    duration_days = discord.ui.TextInput(
        label="Duration Days (1-7)",
        placeholder="1",
        required=False,
        max_length=1
    )

    def __init__(self, cog, user_id, item, item_type, pack_slot=None):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.item = item
        self.item_type = item_type
        self.pack_slot = pack_slot

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start = int(self.starting_price.value)
            buy = int(self.buy_now_price.value) if self.buy_now_price.value else None
            duration = int(self.duration_days.value) if self.duration_days.value else DEFAULT_AUCTION_DAYS
        except ValueError:
            await interaction.response.send_message("Prices and duration must be whole numbers.", ephemeral=True)
            return

        if duration < 1 or duration > MAX_AUCTION_DAYS:
            await interaction.response.send_message(
                f"Auction duration must be between 1 and {MAX_AUCTION_DAYS} days.",
                ephemeral=True
            )
            return
        if start <= 0:
            await interaction.response.send_message("Starting price must be greater than zero.", ephemeral=True)
            return
        if buy is not None and buy <= 0:
            await interaction.response.send_message("Buy now price must be greater than zero.", ephemeral=True)
            return
        if buy is not None and buy < start:
            await interaction.response.send_message("Buy now price cannot be lower than the starting price.", ephemeral=True)
            return

        users_cog = interaction.client.get_cog("Users")
        cards_cog = interaction.client.get_cog("Cards")

        if not self.cog.has_auction_room(self.user_id):
            await interaction.response.send_message(
                f"You can only have {MAX_ACTIVE_AUCTIONS_PER_USER} active auctions at once.",
                ephemeral=True
            )
            return

        if self.item_type == "card":
            if cards_cog.is_card_in_user_team(self.user_id, self.item):
                await interaction.response.send_message(
                    "That card is in your team. Remove or replace it before auctioning it.",
                    ephemeral=True
                )
                return
            users, user_data = cards_cog.get_user_data(self.user_id)
            if not cards_cog.remove_card_from_user(users, self.user_id, self.item):
                await interaction.response.send_message("That card could not be removed from your inventory.", ephemeral=True)
                return
        else:
            profile = users_cog.get_profile_by_id(str(self.user_id))
            removed = users_cog.remove_pack_at_slot(profile, self.pack_slot)
            if removed != self.item:
                await interaction.response.send_message("That pack is no longer in that slot.", ephemeral=True)
                return
            users_cog.save_users()

        self.cog.create_auction(self.user_id, self.item_type, self.item, start, buy, duration)
        await interaction.response.send_message("✅ Auction created!", ephemeral=True)


class BidModal(discord.ui.Modal, title="Place Bid"):
    bid_amount = discord.ui.TextInput(label="Cash Bid Amount")

    def __init__(self, cog, auction):
        super().__init__()
        self.cog = cog
        self.auction = auction

    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount = int(self.bid_amount.value)
        except ValueError:
            await interaction.response.send_message("Bid must be a whole number.", ephemeral=True)
            return
        if amount <= 0:
            await interaction.response.send_message("Bid must be greater than zero.", ephemeral=True)
            return
        error = self.cog.place_bid(self.auction["auction_id"], interaction.user.id, amount)

        if error:
            await interaction.response.send_message(error, ephemeral=True)
        else:
            await interaction.response.send_message(f"✅ Bid placed: {CASH_EMOJI} {amount} cash", ephemeral=True)


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
        held_bid = self.auction.get("current_bid", 0) if self.auction.get("highest_bidder") == str(interaction.user.id) else 0
        if profile["cash"] + held_bid < price:
            await interaction.response.send_message("Not enough cash.", ephemeral=True)
            return

        settings = profile.get("settings", {})
        if settings.get("confirm_auction_buy", True):
            view = ConfirmAuctionBuyView(self.auction, self.cog)
            await interaction.response.send_message("Confirm auction purchase:", view=view, ephemeral=True)
            return

        item_name = self.execute_buy_now(interaction.user.id, users_cog, cards_cog)
        if item_name.startswith("Failed:"):
            await interaction.response.send_message(item_name, ephemeral=True)
            return
        await interaction.response.send_message(f"✅ Purchased **{item_name}** for {CASH_EMOJI} {price} cash!", ephemeral=True)

    def execute_buy_now(self, buyer_id: int, users_cog, cards_cog):
        item_name, error = self.cog.complete_buy_now(self.auction["auction_id"], buyer_id)
        if error:
            return f"Failed: {error}"
        return item_name

    @discord.ui.button(label="Take Down", style=discord.ButtonStyle.red)
    async def take_down(self, interaction, button):
        item_name, error = self.cog.cancel_auction(self.auction["auction_id"], interaction.user.id)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await interaction.response.edit_message(
            content=f"✅ Took down **{item_name}** and returned it to your inventory.",
            view=None
        )


class ConfirmAuctionBuyView(discord.ui.View):
    def __init__(self, auction, cog):
        super().__init__(timeout=30)
        self.auction = auction
        self.cog = cog

    @discord.ui.button(label="Confirm Buy Now", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        users_cog = interaction.client.get_cog("Users")
        cards_cog = interaction.client.get_cog("Cards")
        profile = users_cog.get_profile(interaction.user)
        price = self.auction["buy_now_price"]
        held_bid = self.auction.get("current_bid", 0) if self.auction.get("highest_bidder") == str(interaction.user.id) else 0
        if profile["cash"] + held_bid < price:
            await interaction.response.send_message("Not enough cash.", ephemeral=True)
            return
        item_name = BuyView(self.auction, self.cog).execute_buy_now(interaction.user.id, users_cog, cards_cog)
        if item_name.startswith("Failed:"):
            await interaction.response.edit_message(content=item_name, view=None)
            return
        await interaction.response.edit_message(content=f"✅ Purchased **{item_name}** for {CASH_EMOJI} {price} cash!", view=None)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Purchase cancelled.", view=None)


class AuctionSelect(discord.ui.Select):
    def __init__(self, auctions, cog, page_start=0):
        self.auctions = auctions
        self.cog = cog

        options = [
            discord.SelectOption(
                label=f"{page_start + i + 1}. {self.get_option_label(a)}"[:100],
                description=(
                    f"{CASH_EMOJI} Bid {a['current_bid']} | Buy Now "
                    f"{f'{CASH_EMOJI} {a.get('buy_now_price')}' if a.get('buy_now_price') else 'None'}"
                ),
                value=str(i)
            )
            for i, a in enumerate(auctions)
        ]

        if not options:
            options = [discord.SelectOption(label="No auctions", value="0")]

        super().__init__(placeholder="Select auction", options=options, min_values=1, max_values=1, row=4)

    def get_option_label(self, auction):
        cards_cog = self.cog.bot.get_cog("Cards")
        if auction["item_type"] != "card":
            return f"📦 {str(auction['pack_name']).replace('_', ' ').title()}"[:100]

        card, card_data, player = self.cog.get_auction_card_context(auction, cards_cog)
        rarity = (card or {}).get("rarity", "Unknown")
        symbol = cards_cog.get_rarity_symbol(rarity) if cards_cog else "⚫"
        if not card_data or not player:
            return f"{symbol} Unknown Card"[:100]
        return f"{symbol} {player.get('name', 'Unknown')} | {card_data.get('team', 'Unknown')}"[:100]

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


class AuctionFilterTypeSelect(discord.ui.Select):
    FILTER_LABELS = {
        "team": "Team",
        "rarity": "Rarity",
        "player": "Player",
        "set": "Set",
        "league": "League",
        "role": "Role",
    }

    def __init__(self, view):
        self.auction_view = view
        options = [
            discord.SelectOption(
                label=label,
                value=key,
                default=view.selected_filter_key == key
            )
            for key, label in self.FILTER_LABELS.items()
        ]
        super().__init__(
            placeholder="Choose a filter",
            options=options,
            min_values=1,
            max_values=1,
            row=1
        )

    async def callback(self, interaction: discord.Interaction):
        self.auction_view.selected_filter_key = self.values[0]
        self.auction_view.rebuild_items()
        await interaction.response.edit_message(embed=self.auction_view.build_embed(), view=self.auction_view)


class AuctionFilterValueSelect(discord.ui.Select):
    def __init__(self, view, filter_key, options):
        self.auction_view = view
        self.filter_key = filter_key
        choices = [
            discord.SelectOption(
                label=value[:100],
                value=value[:100],
                default=view.filters.get(filter_key) == value
            )
            for value in options
        ]
        super().__init__(
            placeholder=f"Choose {filter_key}",
            options=choices,
            min_values=1,
            max_values=1,
            row=2
        )

    async def callback(self, interaction: discord.Interaction):
        self.auction_view.filters[self.filter_key] = self.values[0]
        self.auction_view.page = 0
        self.auction_view.reload_auctions()
        self.auction_view.rebuild_items()
        await interaction.response.edit_message(embed=self.auction_view.build_embed(), view=self.auction_view)


class AuctionSortSelect(discord.ui.Select):
    def __init__(self, view):
        self.auction_view = view
        options = [
            discord.SelectOption(label=label, value=value, default=view.sort == value)
            for value, label in view.SORT_LABELS.items()
        ]
        super().__init__(
            placeholder="Sort auctions",
            options=options,
            min_values=1,
            max_values=1,
            row=3
        )

    async def callback(self, interaction: discord.Interaction):
        self.auction_view.sort = self.values[0]
        self.auction_view.page = 0
        self.auction_view.reload_auctions()
        self.auction_view.rebuild_items()
        await interaction.response.edit_message(embed=self.auction_view.build_embed(), view=self.auction_view)


class AuctionView(discord.ui.View):
    SORT_LABELS = {
        "bid_asc": "Lowest Bid",
        "bid_desc": "Highest Bid",
        "buy_asc": "Lowest Buy Now",
        "buy_desc": "Highest Buy Now",
        "player": "Player",
        "team": "Team",
        "rarity": "Rarity",
        "set": "Set",
        "league": "League",
        "role": "Role",
        "time": "Ending Soon",
    }

    def __init__(self, cog, user_id, auctions, filters=None, sort=None):
        super().__init__(timeout=120)
        self.cog = cog
        self.user_id = user_id
        self.auctions = auctions
        self.filters = filters or {}
        self.sort = sort
        self.page = 0
        self.show_mine = False
        self.selected_filter_key = next(iter(self.filters), "team")

        self.update_buttons()
        self.rebuild_items()

    def get_base_auctions(self):
        auctions = self.cog.load_auctions()
        if self.show_mine:
            auctions = [a for a in auctions if a.get("seller_id") == str(self.user_id)]
        return auctions

    def reload_auctions(self):
        cards_cog = self.cog.bot.get_cog("Cards")
        self.auctions = self.cog.filter_and_sort_auctions(
            self.get_base_auctions(),
            self.filters,
            self.sort,
            cards_cog
        )
        if self.page >= self.total_pages():
            self.page = self.total_pages() - 1
        self.update_buttons()

    def get_page_items(self):
        start = self.page_start()
        return self.auctions[start:start + AUCTIONS_PER_PAGE]

    def page_start(self):
        return self.page * AUCTIONS_PER_PAGE

    def total_pages(self):
        return max(1, (len(self.auctions) - 1) // AUCTIONS_PER_PAGE + 1)

    def build_filter_text(self):
        parts = []
        if self.show_mine:
            parts.append("Showing: Your Auctions")
        if self.filters:
            parts.extend(f"{key.capitalize()}: {value}" for key, value in self.filters.items())
        if self.sort:
            parts.append(f"Sort: {self.SORT_LABELS.get(self.sort, self.sort)}")
        return " • ".join(parts)

    def rebuild_items(self):
        self.clear_items()
        self.add_item(self.previous)
        self.add_item(self.next)
        self.add_item(self.refresh)
        self.add_item(self.mine)
        self.add_item(self.clear)
        self.add_item(AuctionFilterTypeSelect(self))

        cards_cog = self.cog.bot.get_cog("Cards")
        filter_base = [
            a for a in self.get_base_auctions()
            if self.cog.auction_matches(
                a,
                {k: v for k, v in self.filters.items() if k != self.selected_filter_key},
                cards_cog
            )
        ]
        filter_options = self.cog.get_filter_options(filter_base, self.selected_filter_key, cards_cog)
        if filter_options:
            self.add_item(AuctionFilterValueSelect(self, self.selected_filter_key, filter_options))

        self.add_item(AuctionSortSelect(self))
        self.add_item(AuctionSelect(self.get_page_items(), self.cog, self.page_start()))

    def build_embed(self):
        cards_cog = self.cog.bot.get_cog("Cards")
        embed = discord.Embed(title="🏛️ Auction House")
        filter_text = self.build_filter_text()
        if filter_text:
            embed.description = filter_text

        if not self.auctions:
            embed.description = f"{filter_text}\nNo auctions available." if filter_text else "No auctions available."
            return embed

        for i, auc in enumerate(self.get_page_items(), start=self.page_start() + 1):
            if auc["item_type"] == "card":
                card, card_data, player = self.cog.get_auction_card_context(auc, cards_cog)
                rarity = (card or {}).get("rarity", "Unknown")
                rarity_symbol = cards_cog.get_rarity_symbol(rarity) if cards_cog else "⚫"

                if card_data and player:
                    name = player.get("name", "Unknown")
                    details = (
                        f"{rarity_symbol} {rarity}\n"
                        f"🛡️ {card_data.get('team', 'Unknown')}\n"
                        f"🎴 {card_data.get('set', 'Unknown')}"
                    )
                else:
                    name = "Unknown Card"
                    details = f"{rarity_symbol} {rarity}"
            else:
                name = str(auc["pack_name"]).replace("_", " ").title()
                details = "📦 Pack"

            buy_now = f"{CASH_EMOJI} {auc['buy_now_price']}" if auc["buy_now_price"] is not None else "None"

            embed.add_field(
                name=f"`#{i}` {name}",
                value=(
                    f"{details}\n"
                    f"{CASH_EMOJI} Bid: {auc['current_bid']}\n"
                    f"🛒 Buy Now: {buy_now}\n"
                    f"⏳ {self.cog.get_time_remaining(auc['expires_at'])}"
                ),
                inline=True
            )

        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages()} • {len(self.auctions)} auctions")
        return embed

    def update_buttons(self):
        self.previous.disabled = self.page <= 0
        self.next.disabled = self.page >= self.total_pages() - 1
        self.mine.label = "All Auctions" if self.show_mine else "My Auctions"
        self.mine.style = discord.ButtonStyle.blurple if self.show_mine else discord.ButtonStyle.secondary
        self.clear.disabled = not self.filters and not self.sort and not self.show_mine

    @discord.ui.button(label="◀", style=discord.ButtonStyle.secondary, row=0)
    async def previous(self, interaction, button):
        self.page -= 1
        self.update_buttons()
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="▶", style=discord.ButtonStyle.secondary, row=0)
    async def next(self, interaction, button):
        self.page += 1
        self.update_buttons()
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.green, row=0)
    async def refresh(self, interaction, button):
        self.reload_auctions()
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="My Auctions", style=discord.ButtonStyle.secondary, row=0)
    async def mine(self, interaction, button):
        self.show_mine = not self.show_mine
        self.page = 0
        self.reload_auctions()
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.red, row=0)
    async def clear(self, interaction, button):
        self.filters = {}
        self.sort = None
        self.show_mine = False
        self.page = 0
        self.reload_auctions()
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)


async def setup(bot):
    await bot.add_cog(Auction(bot))
