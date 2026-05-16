import uuid
from collections import defaultdict
from datetime import datetime, timedelta

import discord
from discord.ext import commands, tasks

from .cards import RARITY_ORDER, RARITY_RANK
from .storage import load_json, save_json

AUCTIONS_PATH = "data/auctions.json"
HISTORY_PATH = "data/auctions_history.json"
DEFAULT_AUCTION_DAYS = 1
MAX_AUCTION_DAYS = 7
AUCTIONS_PER_PAGE = 12
AUTOSELL_PER_PAGE = 20
CASH_EMOJI = "💵"
AUTOSELL_START_PRICES = {
    "silver": 5,
    "gold": 10,
    "diamond": 50,
    "master": 200,
    "challenger": 500,
}
AUTOSELL_DEFAULT_ENABLED = {
    "silver": True,
    "gold": True,
    "diamond": True,
    "master": False,
    "challenger": False,
}


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
        return True

    def load_history(self):
        data = load_json(HISTORY_PATH, default=[])
        return data if isinstance(data, list) else []

    def save_history(self, history):
        save_json(HISTORY_PATH, history)

    # -----------------
    # Helpers
    # -----------------

    def auction_dms_enabled(self, users_cog, user_id):
        profile = users_cog.get_profile_by_id(str(user_id))
        settings = users_cog.normalize_settings(profile) if profile else {}
        return settings.get("dm_auction_notis", True)

    async def get_discord_user(self, user_id):
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return None

        user = self.bot.get_user(user_id)
        if user is not None:
            return user

        try:
            return await self.bot.fetch_user(user_id)
        except Exception:
            return None

    async def send_auction_dm(self, users_cog, user_id, message):
        if not self.auction_dms_enabled(users_cog, user_id):
            return

        user = await self.get_discord_user(user_id)
        if user is None:
            return

        try:
            await user.send(message)
        except Exception:
            pass

    def seller_display_name(self, seller_id):
        users_cog = self.bot.get_cog("Users") if self.bot else None
        seller_id = str(seller_id)

        if users_cog is not None:
            profile = users_cog.get_profile_by_id(seller_id)
            if profile:
                if hasattr(users_cog, "leaderboard_name"):
                    return users_cog.leaderboard_name(seller_id, profile)
                return (
                    profile.get("ign")
                    or profile.get("discord_username")
                    or profile.get("username")
                    or f"User {seller_id[-4:]}"
                )

        try:
            user = self.bot.get_user(int(seller_id)) if self.bot else None
        except (TypeError, ValueError):
            user = None
        return user.display_name if user else f"User {seller_id[-4:]}"

    async def notify_auction_sale(self, auction, winner_id, final_price, item_name=None):
        users_cog = self.bot.get_cog("Users")
        if users_cog is None:
            return

        item_text = f" for **{item_name}**" if item_name else ""
        await self.send_auction_dm(
            users_cog,
            winner_id,
            f"You won auction `{auction['auction_id']}`{item_text} for {CASH_EMOJI} {final_price} cash."
        )
        await self.send_auction_dm(
            users_cog,
            auction["seller_id"],
            f"Your auction `{auction['auction_id']}`{item_text} sold for {CASH_EMOJI} {final_price} cash."
        )

    async def notify_auction_returned(self, auction):
        users_cog = self.bot.get_cog("Users")
        if users_cog is None:
            return

        await self.send_auction_dm(
            users_cog,
            auction["seller_id"],
            f"Your auction `{auction['auction_id']}` ended with no bids. Item returned."
        )

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

    def build_auction_record(self, seller_id, item_type, item_data, start_price, buy_now, duration_days=DEFAULT_AUCTION_DAYS):
        now = datetime.utcnow()
        return {
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

    def create_auction(self, seller_id, item_type, item_data, start_price, buy_now, duration_days=DEFAULT_AUCTION_DAYS):
        auctions = self.load_auctions()
        auctions.append(self.build_auction_record(seller_id, item_type, item_data, start_price, buy_now, duration_days))
        self.save_auctions(auctions)

    # -----------------
    # Bidding
    # -----------------

    def place_bid(self, auction_id, bidder_id, amount):
        auctions = self.load_auctions()
        users_cog = self.bot.get_cog("Users")

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
            minimum_bid = current_bid if highest_bidder is None else current_bid + 1

            if amount < minimum_bid:
                return f"Minimum bid is {CASH_EMOJI} {minimum_bid} cash."

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
        self.bot.loop.create_task(self.notify_auction_sale(target, buyer_id, price, item_name))
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
        progress_flags = {"-progress", "-needed", "-missing"}
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
            elif current in progress_flags:
                filters["progress"] = "Missing Any Rarity"
                i += 1
            elif current in sort_flags:
                i += 1
                value = []

                while (
                    i < len(args)
                    and args[i].lower() not in valid
                    and args[i].lower() not in progress_flags
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
                    and args[i].lower() not in progress_flags
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

    def progress_filter_args(self, filters):
        args = []
        for key in ("team", "player", "set", "league", "role"):
            value = filters.get(key)
            if value:
                args.extend((f"-{key}", value))
        return args

    def add_user_auction_cards_to_rarity_ids(self, user_id, rarity_card_ids, cards_cog):
        seller_id = str(user_id)
        for auction in self.load_auctions():
            if auction.get("seller_id") != seller_id or auction.get("item_type") != "card":
                continue

            card = auction.get("card_instance") or {}
            card_id = card.get("card_id")
            rarity = card.get("rarity")
            if not card_id or rarity not in RARITY_RANK:
                continue

            card_data = cards_cog.get_card_by_id(card_id)
            if card_data:
                card_id = card_data.get("card_id", card_id)
            rarity_card_ids.setdefault(rarity, set()).add(card_id)

    def get_progress_filter_context(self, user_id, filters, cards_cog):
        if user_id is None or cards_cog is None:
            return None

        progress_data, error = cards_cog.get_collection_progress(
            user_id,
            self.progress_filter_args(filters),
        )
        if error or not progress_data:
            return None

        _users, user_data = cards_cog.get_user_data(user_id)
        rarity_card_ids = cards_cog.get_user_rarity_card_ids(user_data)
        self.add_user_auction_cards_to_rarity_ids(user_id, rarity_card_ids, cards_cog)
        matching_ids = {
            card.get("card_id", card.get("id"))
            for card in progress_data["cards"]
        }

        return {
            "matching_ids": matching_ids,
            "rarity_card_ids": rarity_card_ids,
        }

    def progress_filter_label(self, user_id, filters, cards_cog):
        context = self.get_progress_filter_context(user_id, filters, cards_cog)
        if not context:
            return "Missing Any Rarity"
        return "Missing Any Rarity"

    def auction_matches_progress(self, card, card_data, progress_context):
        if not progress_context:
            return False

        card_id = card_data.get("card_id", card_data.get("id"))
        rarity = (card or {}).get("rarity")
        if card_id not in progress_context["matching_ids"] or rarity not in RARITY_RANK:
            return False

        return card_id not in progress_context["rarity_card_ids"].get(rarity, set())

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

    def auction_matches(self, auction, filters, cards_cog, user_id=None, progress_context=None):
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
        if "progress" in filters:
            progress_context = progress_context or self.get_progress_filter_context(user_id, filters, cards_cog)
            if not self.auction_matches_progress(card, card_data, progress_context):
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

    def filter_and_sort_auctions(self, auctions, filters, sort, cards_cog, user_id=None):
        progress_context = None
        if "progress" in filters:
            progress_context = self.get_progress_filter_context(user_id, filters, cards_cog)

        filtered = [
            a for a in auctions
            if self.auction_matches(a, filters, cards_cog, user_id, progress_context)
        ]
        return self.sort_auctions(filtered, sort, cards_cog)

    def card_duplicate_key(self, card):
        return (
            str(card.get("card_id", "")).lower(),
            str(card.get("rarity", "")).lower(),
        )

    def default_autosell_settings(self):
        return {
            rarity.lower(): {
                "enabled": AUTOSELL_DEFAULT_ENABLED[rarity.lower()],
                "starting_price": AUTOSELL_START_PRICES[rarity.lower()],
                "buy_now_price": None,
            }
            for rarity in RARITY_ORDER
        }

    def normalize_autosell_settings(self, settings):
        autosell = settings.get("autosell")
        if not isinstance(autosell, dict):
            autosell = {}
            settings["autosell"] = autosell

        defaults = self.default_autosell_settings()
        legacy_enabled = {
            "master": bool(settings.get("autosell_master", defaults["master"]["enabled"])),
            "challenger": bool(settings.get("autosell_challenger", defaults["challenger"]["enabled"])),
        }

        for rarity_key, default_config in defaults.items():
            config = autosell.get(rarity_key)
            if not isinstance(config, dict):
                config = {}
                autosell[rarity_key] = config

            if "enabled" not in config:
                config["enabled"] = legacy_enabled.get(rarity_key, default_config["enabled"])

            for price_key in ("starting_price", "buy_now_price"):
                value = config.get(price_key, default_config[price_key])
                if value in ("", None):
                    config[price_key] = None if price_key == "buy_now_price" else default_config[price_key]
                    continue
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    value = default_config[price_key]
                if price_key == "starting_price" and value <= 0:
                    value = default_config[price_key]
                if price_key == "buy_now_price" and value is not None and value <= 0:
                    value = None
                config[price_key] = value

        return autosell

    def autosell_prices(self, card, settings):
        rarity = str(card.get("rarity", "")).lower()
        autosell = self.normalize_autosell_settings(settings)
        config = autosell.get(rarity)
        if not config or not config.get("enabled", False):
            return None
        start_price = config.get("starting_price")
        buy_now_price = config.get("buy_now_price")
        if buy_now_price is not None and buy_now_price < start_price:
            buy_now_price = start_price
        return start_price, buy_now_price

    def get_autosell_cards(self, user_id, profile, cards_cog, settings):
        grouped_cards = defaultdict(list)
        for slot_index, card in enumerate(profile.get("cards", [])):
            if not isinstance(card, dict):
                continue
            if cards_cog.is_card_in_user_team(user_id, card):
                continue
            prices = self.autosell_prices(card, settings)
            if prices is None:
                continue
            start_price, buy_now_price = prices
            grouped_cards[self.card_duplicate_key(card)].append((slot_index, card, start_price, buy_now_price))

        sell_cards = []
        for entries in grouped_cards.values():
            if len(entries) >= 2:
                sell_cards.extend(entries[1:])
        return sorted(sell_cards, key=lambda entry: entry[0])

    def format_autosell_line(self, slot_index, card, start_price, buy_now_price, cards_cog):
        card_data = cards_cog.get_card_by_id(card.get("card_id")) or card.get("snapshot", {})
        if not card_data:
            sell_now = f" - Sell Now {CASH_EMOJI} {buy_now_price}" if buy_now_price is not None else ""
            return f"`#{slot_index + 1}` Unknown Card - Start {CASH_EMOJI} {start_price}{sell_now}"

        player = cards_cog.get_player_for_card(card_data)
        player_name = (player or {}).get("name", "Unknown")
        set_name = card_data.get("set", "Unknown Set")
        rarity = card.get("rarity", "Unknown Rarity")
        rarity_symbol = cards_cog.get_rarity_symbol(rarity)
        sell_now = f" - Sell Now {CASH_EMOJI} {buy_now_price}" if buy_now_price is not None else ""
        return f"`#{slot_index + 1}` {rarity_symbol} {player_name} {set_name} - Start {CASH_EMOJI} {start_price}{sell_now}"

    def build_autosell_embed(self, user_display_name, sell_cards, cards_cog, page=0):
        total_pages = max(1, (len(sell_cards) - 1) // AUTOSELL_PER_PAGE + 1)
        page = min(max(page, 0), total_pages - 1)
        start = page * AUTOSELL_PER_PAGE
        page_cards = sell_cards[start:start + AUTOSELL_PER_PAGE]
        lines = [
            self.format_autosell_line(slot_index, card, start_price, buy_now_price, cards_cog)
            for slot_index, card, start_price, buy_now_price in page_cards
        ]

        embed = discord.Embed(
            title=f"{user_display_name}'s Autosell Review",
            description=(
                f"You are about to autosell {len(sell_cards)} cards onto the auction! Continue?\n\n"
                + ("\n".join(lines) if lines else "No duplicate cards are eligible for autosell.")
            ),
            color=discord.Color.dark_grey()
        )
        embed.set_footer(text=f"Page {page + 1}/{total_pages} • {len(sell_cards)} cards will be auctioned")
        return embed

    def build_autosell_settings_embed(self, profile, selected_rarity=None):
        settings = self.bot.get_cog("Users").normalize_settings(profile)
        autosell = self.normalize_autosell_settings(settings)
        selected_rarity = selected_rarity or RARITY_ORDER[0]
        lines = []
        for rarity in RARITY_ORDER:
            rarity_key = rarity.lower()
            config = autosell[rarity_key]
            marker = ">" if rarity == selected_rarity else " "
            status = "ON" if config.get("enabled") else "OFF"
            buy_now = config.get("buy_now_price")
            buy_now_text = f"{CASH_EMOJI} {buy_now}" if buy_now is not None else "None"
            lines.append(
                f"{marker} **{rarity}**: {status} | Start {CASH_EMOJI} {config['starting_price']} | Sell Now {buy_now_text}"
            )

        embed = discord.Embed(
            title="Autosell Settings",
            description="\n".join(lines),
            color=discord.Color.dark_grey()
        )
        embed.set_footer(text="Use the dropdown to pick a rarity, then toggle it or edit its prices.")
        return embed

    def execute_autosell(self, user_id, target_instance_ids):
        cards_cog = self.bot.get_cog("Cards")
        users_cog = self.bot.get_cog("Users")
        if cards_cog is None or users_cog is None:
            return [], "Autosell is unavailable right now."

        profile = users_cog.get_profile_by_id(str(user_id))
        settings = users_cog.normalize_settings(profile)
        eligible_cards = self.get_autosell_cards(user_id, profile, cards_cog, settings)
        target_instance_ids = set(target_instance_ids)
        sell_cards = [
            entry for entry in eligible_cards
            if entry[1].get("instance_id") in target_instance_ids
        ]

        if not sell_cards:
            return [], "No duplicate cards are still eligible for autosell."

        auctions = self.load_auctions()
        created_cards = []
        for slot_index, card, start_price, buy_now_price in sorted(sell_cards, key=lambda entry: entry[0], reverse=True):
            if slot_index >= len(profile.get("cards", [])) or profile["cards"][slot_index] is not card:
                continue
            profile["cards"][slot_index] = None
            auctions.append(self.build_auction_record(user_id, "card", card, start_price, buy_now_price, DEFAULT_AUCTION_DAYS))
            created_cards.append((slot_index, card, start_price, buy_now_price))

        if not created_cards:
            return [], "No duplicate cards could be autosold. Your inventory changed before autosell finished."

        users_cog.save_users()
        self.save_auctions(auctions)
        return created_cards, None

    def get_filter_options(self, auctions, filter_key, cards_cog, user_id=None, filters=None):
        if filter_key == "progress":
            return []

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
            await self.notify_auction_sale(auction, winner, auction["current_bid"])
        else:
            await self.notify_auction_returned(auction)

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
        if "progress" in filters:
            filters["progress"] = self.progress_filter_label(ctx.author.id, filters, cards_cog)
        filtered = self.filter_and_sort_auctions(auctions, filters, sort, cards_cog, ctx.author.id)

        view = AuctionView(self, ctx.author.id, filtered, filters, sort)
        await ctx.send(embed=view.build_embed(), view=view)

    @commands.command()
    async def autosell(self, ctx, *args):
        cards_cog = self.bot.get_cog("Cards")
        users_cog = self.bot.get_cog("Users")
        if cards_cog is None or users_cog is None:
            await ctx.send("Autosell is unavailable right now.")
            return

        profile = users_cog.get_profile(ctx.author)
        settings = users_cog.normalize_settings(profile)
        if any(str(arg).lower() in {"-settings", "settings"} for arg in args):
            self.normalize_autosell_settings(settings)
            users_cog.save_users()
            view = AutosellSettingsView(self, ctx.author.id, profile)
            await ctx.send(embed=view.build_embed(), view=view)
            return

        if args:
            await ctx.send("Use `.autosell` to review duplicate autosell auctions or `.autosell -settings` to edit autosell settings.")
            return

        sell_cards = self.get_autosell_cards(ctx.author.id, profile, cards_cog, settings)

        if not sell_cards:
            await ctx.send("No duplicate cards are eligible for autosell.")
            return

        view = AutosellReviewView(self, ctx.author.id, ctx.author.display_name, sell_cards)
        await ctx.send(embed=view.build_embed(), view=view)


class AutosellSettingsView(discord.ui.View):
    def __init__(self, cog, author_id, profile):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id
        self.profile = profile
        self.selected_rarity = RARITY_ORDER[0]
        self.rarity_select.options = self.build_options()
        self.refresh_buttons()

    def settings(self):
        users_cog = self.cog.bot.get_cog("Users")
        return users_cog.normalize_settings(self.profile)

    def autosell_settings(self):
        return self.cog.normalize_autosell_settings(self.settings())

    def build_options(self):
        autosell = self.autosell_settings()
        options = []
        for rarity in RARITY_ORDER:
            config = autosell[rarity.lower()]
            status = "ON" if config.get("enabled") else "OFF"
            options.append(discord.SelectOption(
                label=rarity,
                value=rarity,
                description=f"{status} | Start {config['starting_price']} | Sell Now {config.get('buy_now_price') or 'None'}",
                default=rarity == self.selected_rarity,
            ))
        return options

    def refresh_buttons(self):
        config = self.autosell_settings()[self.selected_rarity.lower()]
        self.toggle_button.label = f"{'Disable' if config.get('enabled') else 'Enable'} {self.selected_rarity}"
        self.toggle_button.style = discord.ButtonStyle.danger if config.get("enabled") else discord.ButtonStyle.success

    def build_embed(self):
        return self.cog.build_autosell_settings_embed(self.profile, self.selected_rarity)

    async def redraw(self, interaction):
        self.rarity_select.options = self.build_options()
        self.refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You can only edit your own autosell settings.", ephemeral=True)
            return False
        return True

    @discord.ui.select(placeholder="Choose rarity", min_values=1, max_values=1, options=[
        discord.SelectOption(label=rarity, value=rarity) for rarity in RARITY_ORDER
    ])
    async def rarity_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.selected_rarity = select.values[0]
        await self.redraw(interaction)

    @discord.ui.button(label="Toggle Rarity", style=discord.ButtonStyle.secondary)
    async def toggle_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        autosell = self.autosell_settings()
        config = autosell[self.selected_rarity.lower()]
        config["enabled"] = not config.get("enabled", False)
        self.cog.bot.get_cog("Users").save_users()
        await self.redraw(interaction)

    @discord.ui.button(label="Edit Prices", style=discord.ButtonStyle.primary)
    async def edit_prices_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AutosellPriceModal(self))


class AutosellPriceModal(discord.ui.Modal, title="Edit Autosell Prices"):
    def __init__(self, view: AutosellSettingsView):
        self.settings_view = view
        config = view.autosell_settings()[view.selected_rarity.lower()]
        super().__init__(title=f"{view.selected_rarity} Autosell Prices")
        self.starting_price.default = str(config.get("starting_price") or "")
        self.buy_now_price.default = "" if config.get("buy_now_price") is None else str(config.get("buy_now_price"))

    starting_price = discord.ui.TextInput(label="Starting Cash Price", required=True, max_length=10)
    buy_now_price = discord.ui.TextInput(
        label="Sell Now Cash Price",
        placeholder="Leave blank for no sell now price",
        required=False,
        max_length=10
    )

    async def on_submit(self, interaction: discord.Interaction):
        try:
            start = int(str(self.starting_price.value).strip())
            buy_text = str(self.buy_now_price.value).strip()
            buy_now = int(buy_text) if buy_text else None
        except ValueError:
            await interaction.response.send_message("Prices must be whole numbers.", ephemeral=True)
            return

        if start <= 0:
            await interaction.response.send_message("Starting price must be greater than zero.", ephemeral=True)
            return
        if buy_now is not None and buy_now <= 0:
            await interaction.response.send_message("Sell now price must be greater than zero.", ephemeral=True)
            return
        if buy_now is not None and buy_now < start:
            await interaction.response.send_message("Sell now price cannot be lower than the starting price.", ephemeral=True)
            return

        config = self.settings_view.autosell_settings()[self.settings_view.selected_rarity.lower()]
        config["starting_price"] = start
        config["buy_now_price"] = buy_now
        self.settings_view.cog.bot.get_cog("Users").save_users()
        self.settings_view.rarity_select.options = self.settings_view.build_options()
        self.settings_view.refresh_buttons()
        await interaction.response.edit_message(embed=self.settings_view.build_embed(), view=self.settings_view)


class AutosellReviewView(discord.ui.View):
    def __init__(self, cog, author_id, user_display_name, sell_cards):
        super().__init__(timeout=120)
        self.cog = cog
        self.author_id = author_id
        self.user_display_name = user_display_name
        self.sell_cards = sell_cards
        self.target_instance_ids = [
            card.get("instance_id")
            for _, card, _, _ in sell_cards
            if card.get("instance_id")
        ]
        self.page = 0
        self.update_buttons()

    def total_pages(self):
        if not self.sell_cards:
            return 1
        return (len(self.sell_cards) - 1) // AUTOSELL_PER_PAGE + 1

    def build_embed(self):
        cards_cog = self.cog.bot.get_cog("Cards")
        return self.cog.build_autosell_embed(self.user_display_name, self.sell_cards, cards_cog, self.page)

    def update_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages() - 1

    def disable_all_items(self):
        for item in self.children:
            item.disabled = True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You cannot use someone else's autosell buttons.", ephemeral=True)
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

    @discord.ui.button(label="Confirm Autosell", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        created_cards, error = self.cog.execute_autosell(self.author_id, self.target_instance_ids)
        self.disable_all_items()
        if error:
            await interaction.response.edit_message(content=error, embed=None, view=self)
            return
        await interaction.response.edit_message(
            content=f"Created {len(created_cards)} autosell auctions.",
            embed=self.build_embed(),
            view=self
        )

    @discord.ui.button(label="Decline Autosell", style=discord.ButtonStyle.danger)
    async def decline_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.disable_all_items()
        await interaction.response.edit_message(content="Autosell cancelled. No cards were auctioned.", embed=None, view=self)

    async def on_timeout(self):
        self.disable_all_items()


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
    def __init__(self, cog, user_id, pack_slot, pack_name):
        super().__init__()
        self.cog = cog
        self.user_id = user_id
        self.pack_slot = pack_slot
        self.pack_name = pack_name

    @discord.ui.button(label="Create Pack Auction", style=discord.ButtonStyle.green)
    async def open_modal(self, interaction: discord.Interaction, button):
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
    def __init__(self, auction, cog, viewer_id=None):
        super().__init__()
        self.auction = auction
        self.cog = cog
        self.viewer_id = str(viewer_id) if viewer_id is not None else None
        is_seller = self.viewer_id is not None and auction.get("seller_id") == self.viewer_id

        if is_seller:
            self.remove_item(self.bid)
        if is_seller or auction.get("buy_now_price") is None:
            self.remove_item(self.buy)
        if not is_seller:
            self.remove_item(self.take_down)

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
        item_name = BuyView(self.auction, self.cog, interaction.user.id).execute_buy_now(interaction.user.id, users_cog, cards_cog)
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
            view=BuyView(auction, self.cog, interaction.user.id),
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
        "progress": "Missing Progress",
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
        if self.auction_view.selected_filter_key == "progress":
            cards_cog = self.auction_view.cog.bot.get_cog("Cards")
            self.auction_view.filters["progress"] = self.auction_view.cog.progress_filter_label(
                self.auction_view.user_id,
                self.auction_view.filters,
                cards_cog,
            )
            self.auction_view.page = 0
            self.auction_view.reload_auctions()
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
        if "progress" in self.filters:
            self.filters["progress"] = self.cog.progress_filter_label(self.user_id, self.filters, cards_cog)
        self.auctions = self.cog.filter_and_sort_auctions(
            self.get_base_auctions(),
            self.filters,
            self.sort,
            cards_cog,
            self.user_id
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
            for key, value in self.filters.items():
                if key == "progress":
                    parts.append(f"Progress: {value}")
                else:
                    parts.append(f"{key.capitalize()}: {value}")
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
                cards_cog,
                self.user_id
            )
        ]
        filter_options = self.cog.get_filter_options(
            filter_base,
            self.selected_filter_key,
            cards_cog,
            self.user_id,
            self.filters,
        )
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
            seller_name = self.cog.seller_display_name(auc.get("seller_id"))

            embed.add_field(
                name=f"`#{i}` {name}",
                value=(
                    f"{details}\n"
                    f"Seller: {seller_name}\n"
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
