import json
import uuid
from datetime import datetime, timedelta

import discord
from discord.ext import commands

TRADES_PATH = "data/trades.json"
CASH_EMOJI = "💵"


class Trades(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.active_trades = {}

    # -----------------
    # JSON
    # -----------------

    def load_trades(self):
        try:
            with open(TRADES_PATH, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
                return []
        except:
            return []

    def save_trades(self, trades):
        with open(TRADES_PATH, "w") as f:
            json.dump(trades, f, indent=4)

    async def refresh_trade_message(self, trade_id):
        trade = self.active_trades.get(trade_id)
        if not trade:
            return
        message_id = trade.get("message_id")
        channel_id = trade.get("channel_id")
        if not message_id or not channel_id:
            return
        channel = self.bot.get_channel(channel_id)
        if not channel:
            return
        try:
            message = await channel.fetch_message(message_id)
            view = TradeView(self, trade_id)
            await message.edit(embed=view.build_embed(), view=view)
        except Exception:
            return

    # -----------------
    # COMMAND
    # -----------------

    @commands.command()
    async def trade(self, ctx, member: discord.Member):
        if member.id == ctx.author.id:
            await ctx.send("You cannot trade yourself.")
            return
        for t in self.active_trades.values():
            ids = {t.get("user1"), t.get("user2")}
            if str(ctx.author.id) in ids or str(member.id) in ids:
                await ctx.send("One of those users is already in an active trade.")
                return

        view = TradeRequestView(self, ctx.author, member)

        await ctx.send(
            f"{member.mention}, {ctx.author.mention} wants to trade with you.",
            view=view
        )

    # -----------------
    # EXECUTE TRADE
    # -----------------

    def execute_trade(self, trade_id):
        trade = self.active_trades[trade_id]

        users_cog = self.bot.get_cog("Users")
        if not users_cog:
            return False, "User data is not available right now."

        u1 = trade["user1"]
        u2 = trade["user2"]

        p1 = users_cog.get_profile_by_id(u1)
        p2 = users_cog.get_profile_by_id(u2)

        o1 = trade["offers"][u1]
        o2 = trade["offers"][u2]

        def validate_offer(profile, offer, label):
            if offer["cash"] < 0:
                return f"{label}'s cash offer is invalid."
            if profile.get("cash", 0) < offer["cash"]:
                return f"{label} no longer has enough cash."

            owned_card_ids = {
                card.get("instance_id")
                for card in profile.get("cards", [])
                if isinstance(card, dict) and card.get("instance_id")
            }
            offered_card_ids = []
            for card in offer["cards"]:
                instance_id = card.get("instance_id") if isinstance(card, dict) else None
                if not instance_id:
                    return f"{label}'s offer contains an invalid card."
                offered_card_ids.append(instance_id)
                if instance_id not in owned_card_ids:
                    return f"{label} no longer owns one of the offered cards."
            if len(offered_card_ids) != len(set(offered_card_ids)):
                return f"{label}'s offer contains the same card more than once."

            packs = profile.get("packs", [])
            offered_pack_slots = []
            for pack_offer in offer["packs"]:
                if isinstance(pack_offer, dict):
                    slot = pack_offer.get("slot")
                    pack_id = pack_offer.get("pack_id")
                    if not isinstance(slot, int) or slot < 0 or slot >= len(packs):
                        return f"{label}'s offer contains an invalid pack slot."
                    if packs[slot] != pack_id:
                        return f"{label} no longer owns one of the offered packs."
                    offered_pack_slots.append(slot)
                else:
                    matching_slots = [i for i, existing in enumerate(packs) if existing == pack_offer]
                    if not matching_slots:
                        return f"{label} no longer owns one of the offered packs."
                    offered_pack_slots.append(matching_slots[0])
            if len(offered_pack_slots) != len(set(offered_pack_slots)):
                return f"{label}'s offer contains the same pack more than once."

            return None

        for profile, offer, label in ((p1, o1, "User 1"), (p2, o2, "User 2")):
            error = validate_offer(profile, offer, label)
            if error:
                return False, error

        def remove_card_slot(profile, card):
            target = card.get("instance_id")
            for i, existing in enumerate(profile.get("cards", [])):
                if isinstance(existing, dict) and existing.get("instance_id") == target:
                    profile["cards"][i] = None
                    return True
            return False

        def add_card_slot(profile, card):
            profile.setdefault("cards", [])
            for i, existing in enumerate(profile["cards"]):
                if existing is None:
                    profile["cards"][i] = card
                    return
            profile["cards"].append(card)

        # --- CASH ---
        p1["cash"] -= o1["cash"]
        p2["cash"] += o1["cash"]

        p2["cash"] -= o2["cash"]
        p1["cash"] += o2["cash"]

        # --- CARDS ---
        for card in o1["cards"]:
            if not remove_card_slot(p1, card):
                return False, "User 1 no longer owns one of the offered cards."
            add_card_slot(p2, card)

        for card in o2["cards"]:
            if not remove_card_slot(p2, card):
                return False, "User 2 no longer owns one of the offered cards."
            add_card_slot(p1, card)

        # --- PACKS ---
        def pack_offer_id(offer):
            if isinstance(offer, dict):
                return offer.get("pack_id")
            return offer

        def remove_pack_offer(profile, offer):
            if isinstance(offer, dict) and "slot" in offer:
                removed = users_cog.remove_pack_at_slot(profile, offer["slot"])
                return removed == offer.get("pack_id")

            for i, existing in enumerate(profile.get("packs", [])):
                if existing == offer:
                    profile["packs"][i] = None
                    return True
            return False

        for pack_offer in o1["packs"]:
            if remove_pack_offer(p1, pack_offer):
                users_cog.add_pack_to_first_slot(p2, pack_offer_id(pack_offer))

        for pack_offer in o2["packs"]:
            if remove_pack_offer(p2, pack_offer):
                users_cog.add_pack_to_first_slot(p1, pack_offer_id(pack_offer))

        users_cog.save_users()

        # --- SAVE HISTORY ---
        history = self.load_trades()

        history.append({
            "trade_id": trade_id,
            "user1_id": u1,
            "user2_id": u2,
            "user1_offer": o1,
            "user2_offer": o2,
            "completed_at": datetime.utcnow().isoformat()
        })

        self.save_trades(history)

        del self.active_trades[trade_id]
        return True, "Trade completed."


# -----------------
# REQUEST VIEW
# -----------------

class TradeRequestView(discord.ui.View):
    def __init__(self, cog, sender, receiver):
        super().__init__()
        self.cog = cog
        self.sender = sender
        self.receiver = receiver

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.green)
    async def accept(self, interaction, button):
        if interaction.user != self.receiver:
            return
        if getattr(self, "accepted", False):
            await interaction.response.send_message("This trade request has already been handled.", ephemeral=True)
            return
        self.accepted = True

        trade_id = str(uuid.uuid4())

        self.cog.active_trades[trade_id] = {
            "user1": str(self.sender.id),
            "user2": str(self.receiver.id),
            "offers": {
                str(self.sender.id): {"cards": [], "packs": [], "cash": 0},
                str(self.receiver.id): {"cards": [], "packs": [], "cash": 0}
            },
            "confirmed": {
                str(self.sender.id): False,
                str(self.receiver.id): False
            },
            "can_confirm_at": datetime.utcnow().isoformat()
        }

        await interaction.response.edit_message(content="Trade request accepted.", view=None)
        msg = await interaction.channel.send("Trade started.", embed=TradeView(self.cog, trade_id).build_embed(), view=TradeView(self.cog, trade_id))
        self.cog.active_trades[trade_id]["message_id"] = msg.id
        self.cog.active_trades[trade_id]["channel_id"] = msg.channel.id
        await self.cog.refresh_trade_message(trade_id)

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.red)
    async def decline(self, interaction, button):
        if interaction.user != self.receiver:
            return

        await interaction.response.send_message("Trade declined.")


# -----------------
# MAIN TRADE VIEW
# -----------------

class TradeView(discord.ui.View):
    def __init__(self, cog, trade_id):
        super().__init__(timeout=300)
        self.cog = cog
        self.trade_id = trade_id

    def get_trade(self):
        return self.cog.active_trades[self.trade_id]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        trade = self.get_trade()
        allowed = {trade["user1"], trade["user2"]}
        if str(interaction.user.id) not in allowed:
            await interaction.response.send_message("Only trade participants can use these buttons.", ephemeral=True)
            return False
        return True

    def build_embed(self):
        trade = self.get_trade()
        cards_cog = self.cog.bot.get_cog("Cards")

        def display_name(uid):
            user = self.cog.bot.get_user(int(uid))
            return user.display_name if user else f"User {uid}"

        def shorten_lines(lines):
            text = "\n".join(lines)
            if len(text) <= 1024:
                return text

            kept = []
            current = 0
            for line in lines:
                extra = len(line) + (1 if kept else 0)
                if current + extra + len("\n...") > 1024:
                    break
                kept.append(line)
                current += extra
            return "\n".join(kept + ["..."])

        def format_card(card):
            if not isinstance(card, dict):
                return "Unknown card"
            if cards_cog:
                card_data = cards_cog.get_card_by_id(card.get("card_id"))
                if not card_data and card.get("snapshot"):
                    card_data = card["snapshot"]
                player = cards_cog.get_player_for_card(card_data) if card_data else None
                if card_data and player:
                    rarity = card.get("rarity", "Unknown")
                    rarity_symbol = cards_cog.get_rarity_symbol(rarity)
                    player_name = player.get("name", "Unknown")
                    set_name = card_data.get("set", "Unknown Set")
                    return f"{rarity_symbol} {player_name} {set_name}"
            return card.get("card_id", "Unknown card")

        def format_pack(pack_offer):
            pack_id = pack_offer.get("pack_id") if isinstance(pack_offer, dict) else pack_offer
            pack = cards_cog.packs.get(pack_id) if cards_cog and pack_id else None
            pack_name = pack.get("name") if pack else pack_id
            return pack_name or "Unknown pack"

        def format_offer(uid):
            offer = trade["offers"][uid]
            lines = [f"Status: {'Accepted' if trade['confirmed'][uid] else 'Not accepted'}"]

            if offer["cash"]:
                lines.append(f"{CASH_EMOJI} Cash: {offer['cash']}")

            if offer["cards"]:
                lines.append("Cards:")
                lines.extend(f"- {format_card(card)}" for card in offer["cards"])

            if offer["packs"]:
                lines.append("Packs:")
                lines.extend(f"- {format_pack(pack_offer)}" for pack_offer in offer["packs"])

            if len(lines) == 1:
                lines.append("No items offered yet.")

            return shorten_lines(lines)

        embed = discord.Embed(title="Trade")

        embed.add_field(
            name=display_name(trade["user1"]),
            value=format_offer(trade["user1"]),
            inline=True
        )

        embed.add_field(
            name=display_name(trade["user2"]),
            value=format_offer(trade["user2"]),
            inline=True
        )

        can_confirm_at = datetime.fromisoformat(trade.get("can_confirm_at", datetime.utcnow().isoformat()))
        seconds_left = max(0, int((can_confirm_at - datetime.utcnow()).total_seconds()))
        if seconds_left > 0:
            embed.set_footer(text=f"Confirm unlocks in {seconds_left}s after latest change.")
        else:
            embed.set_footer(text="Both sides can confirm now.")

        return embed

    # ---------- BUTTONS ----------

    @discord.ui.button(label="Add Card", style=discord.ButtonStyle.blurple)
    async def add_card(self, interaction, button):
        await interaction.response.send_modal(
            AddCardModal(self.cog, self.trade_id)
        )

    @discord.ui.button(label="Add Pack", style=discord.ButtonStyle.blurple)
    async def add_pack(self, interaction, button):
        await interaction.response.send_modal(
            AddPackModal(self.cog, self.trade_id)
        )

    @discord.ui.button(label=f"{CASH_EMOJI} Add Cash", style=discord.ButtonStyle.secondary)
    async def add_cash(self, interaction, button):
        await interaction.response.send_modal(
            AddCashModal(self.cog, self.trade_id)
        )

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green)
    async def confirm(self, interaction, button):
        trade = self.get_trade()
        uid = str(interaction.user.id)
        can_confirm_at = datetime.fromisoformat(trade.get("can_confirm_at", datetime.utcnow().isoformat()))
        if datetime.utcnow() < can_confirm_at:
            wait = int((can_confirm_at - datetime.utcnow()).total_seconds()) + 1
            await interaction.response.send_message(f"Please wait {wait}s before confirming.", ephemeral=True)
            return

        trade["confirmed"][uid] = True

        if all(trade["confirmed"].values()):
            success, message = self.cog.execute_trade(self.trade_id)
            if not success:
                for participant_id in trade["confirmed"]:
                    trade["confirmed"][participant_id] = False
                await interaction.response.send_message(message, ephemeral=True)
                await self.cog.refresh_trade_message(self.trade_id)
                return
            await interaction.response.edit_message(content="Trade completed.", embed=None, view=None)
            return
        else:
            await interaction.response.send_message(
                "Waiting for other user.",
                ephemeral=True
            )
            await self.cog.refresh_trade_message(self.trade_id)

    @discord.ui.button(label="Cancel Trade", style=discord.ButtonStyle.red)
    async def cancel(self, interaction, button):
        trade = self.get_trade()
        if str(interaction.user.id) not in {trade["user1"], trade["user2"]}:
            await interaction.response.send_message("Only trade participants can cancel.", ephemeral=True)
            return
        del self.cog.active_trades[self.trade_id]
        await interaction.response.edit_message(content="Trade cancelled.", embed=None, view=None)
        return


# -----------------
# MODALS
# -----------------

class AddCardModal(discord.ui.Modal, title="Add Card"):
    index = discord.ui.TextInput(label="Inventory Index")

    def __init__(self, cog, trade_id):
        super().__init__()
        self.cog = cog
        self.trade_id = trade_id

    async def on_submit(self, interaction):
        cards_cog = interaction.client.get_cog("Cards")

        try:
            index = int(self.index.value)
        except ValueError:
            await interaction.response.send_message("Inventory index must be a number.", ephemeral=True)
            return

        owned_card, _, _, error = cards_cog.get_owned_card_by_inventory_number(
            interaction.user.id, index
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        trade = self.cog.active_trades[self.trade_id]
        uid = str(interaction.user.id)

        instance_id = owned_card.get("instance_id")
        for offered_card in trade["offers"][uid]["cards"]:
            if offered_card.get("instance_id") == instance_id:
                await interaction.response.send_message("That card is already in your offer.", ephemeral=True)
                return

        trade["offers"][uid]["cards"].append(owned_card)
        trade["confirmed"][trade["user1"]] = False
        trade["confirmed"][trade["user2"]] = False
        trade["can_confirm_at"] = (datetime.utcnow() + timedelta(seconds=3)).isoformat()

        await interaction.response.send_message("Card added.", ephemeral=True)
        await self.cog.refresh_trade_message(self.trade_id)


class AddPackModal(discord.ui.Modal, title="Add Pack"):
    index = discord.ui.TextInput(label="Pack Index")

    def __init__(self, cog, trade_id):
        super().__init__()
        self.cog = cog
        self.trade_id = trade_id

    async def on_submit(self, interaction):
        users_cog = interaction.client.get_cog("Users")

        profile = users_cog.get_profile(interaction.user)
        try:
            index = int(self.index.value)
        except ValueError:
            await interaction.response.send_message("Pack index must be a number.", ephemeral=True)
            return

        if index < 1 or index > len(profile.get("packs", [])):
            await interaction.response.send_message("Invalid pack index.", ephemeral=True)
            return

        pack = profile["packs"][index - 1]
        if pack is None:
            await interaction.response.send_message("That pack slot is empty.", ephemeral=True)
            return

        trade = self.cog.active_trades[self.trade_id]
        uid = str(interaction.user.id)
        slot = index - 1

        for pack_offer in trade["offers"][uid]["packs"]:
            if isinstance(pack_offer, dict) and pack_offer.get("slot") == slot:
                await interaction.response.send_message("That pack is already in your offer.", ephemeral=True)
                return

        trade["offers"][uid]["packs"].append({"slot": slot, "pack_id": pack})
        trade["confirmed"][trade["user1"]] = False
        trade["confirmed"][trade["user2"]] = False
        trade["can_confirm_at"] = (datetime.utcnow() + timedelta(seconds=3)).isoformat()

        await interaction.response.send_message("Pack added.", ephemeral=True)
        await self.cog.refresh_trade_message(self.trade_id)


class AddCashModal(discord.ui.Modal, title="Add Cash"):
    amount = discord.ui.TextInput(label="Cash Amount")

    def __init__(self, cog, trade_id):
        super().__init__()
        self.cog = cog
        self.trade_id = trade_id

    async def on_submit(self, interaction):
        users_cog = interaction.client.get_cog("Users")

        try:
            amount = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("Cash amount must be a number.", ephemeral=True)
            return

        if amount < 0:
            await interaction.response.send_message("Cash amount cannot be negative.", ephemeral=True)
            return

        profile = users_cog.get_profile(interaction.user)

        if profile["cash"] < amount:
            await interaction.response.send_message("Not enough cash.", ephemeral=True)
            return

        trade = self.cog.active_trades[self.trade_id]
        uid = str(interaction.user.id)

        trade["offers"][uid]["cash"] = amount
        trade["confirmed"][trade["user1"]] = False
        trade["confirmed"][trade["user2"]] = False
        trade["can_confirm_at"] = (datetime.utcnow() + timedelta(seconds=3)).isoformat()

        await interaction.response.send_message("Cash added.", ephemeral=True)
        await self.cog.refresh_trade_message(self.trade_id)


async def setup(bot):
    await bot.add_cog(Trades(bot))
