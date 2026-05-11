import json
import uuid
from datetime import datetime, timedelta

import discord
from discord.ext import commands

TRADES_PATH = "data/trades.json"


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

        u1 = trade["user1"]
        u2 = trade["user2"]

        p1 = users_cog.get_profile_by_id(u1)
        p2 = users_cog.get_profile_by_id(u2)

        o1 = trade["offers"][u1]
        o2 = trade["offers"][u2]

        # --- GOLD ---
        if p1["gold"] < o1["gold"] or p2["gold"] < o2["gold"]:
            return

        p1["gold"] -= o1["gold"]
        p2["gold"] += o1["gold"]

        p2["gold"] -= o2["gold"]
        p1["gold"] += o2["gold"]

        # --- CARDS ---
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

        for card in o1["cards"]:
            remove_card_slot(p1, card)
            add_card_slot(p2, card)

        for card in o2["cards"]:
            remove_card_slot(p2, card)
            add_card_slot(p1, card)

        # --- PACKS ---
        def remove_pack_slot(profile, pack):
            for i, existing in enumerate(profile.get("packs", [])):
                if existing == pack:
                    profile["packs"][i] = None
                    return True
            return False

        def add_pack_slot(profile, pack):
            profile.setdefault("packs", [])
            for i, existing in enumerate(profile["packs"]):
                if existing is None:
                    profile["packs"][i] = pack
                    return
            profile["packs"].append(pack)

        for pack in o1["packs"]:
            remove_pack_slot(p1, pack)
            add_pack_slot(p2, pack)

        for pack in o2["packs"]:
            remove_pack_slot(p2, pack)
            add_pack_slot(p1, pack)

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
                str(self.sender.id): {"cards": [], "packs": [], "gold": 0},
                str(self.receiver.id): {"cards": [], "packs": [], "gold": 0}
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

        def format_offer(uid):
            offer = trade["offers"][uid]
            return (
                f"Cards: {len(offer['cards'])}\n"
                f"Packs: {len(offer['packs'])}\n"
                f"Gold: {offer['gold']}"
            )

        embed = discord.Embed(title="Trade")

        embed.add_field(
            name=f"User 1",
            value=format_offer(trade["user1"]),
            inline=True
        )

        embed.add_field(
            name=f"User 2",
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

    @discord.ui.button(label="Add Gold", style=discord.ButtonStyle.secondary)
    async def add_gold(self, interaction, button):
        await interaction.response.send_modal(
            AddGoldModal(self.cog, self.trade_id)
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
            self.cog.execute_trade(self.trade_id)
            await interaction.response.edit_message(
                content="✅ Trade completed.",
                view=None
            )
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
        await interaction.response.edit_message(content="❌ Trade cancelled.", embed=None, view=None)


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

        index = int(self.index.value)

        owned_card, *_ = cards_cog.get_owned_card_by_inventory_number(
            interaction.user.id, index
        )

        trade = self.cog.active_trades[self.trade_id]
        uid = str(interaction.user.id)

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
        index = int(self.index.value)

        pack = profile["packs"][index - 1]
        if pack is None:
            await interaction.response.send_message("That pack slot is empty.", ephemeral=True)
            return

        trade = self.cog.active_trades[self.trade_id]
        uid = str(interaction.user.id)

        trade["offers"][uid]["packs"].append(pack)
        trade["confirmed"][trade["user1"]] = False
        trade["confirmed"][trade["user2"]] = False
        trade["can_confirm_at"] = (datetime.utcnow() + timedelta(seconds=3)).isoformat()

        await interaction.response.send_message("Pack added.", ephemeral=True)
        await self.cog.refresh_trade_message(self.trade_id)


class AddGoldModal(discord.ui.Modal, title="Add Gold"):
    amount = discord.ui.TextInput(label="Gold Amount")

    def __init__(self, cog, trade_id):
        super().__init__()
        self.cog = cog
        self.trade_id = trade_id

    async def on_submit(self, interaction):
        users_cog = interaction.client.get_cog("Users")

        amount = int(self.amount.value)
        profile = users_cog.get_profile(interaction.user)

        if profile["gold"] < amount:
            await interaction.response.send_message("Not enough gold.", ephemeral=True)
            return

        trade = self.cog.active_trades[self.trade_id]
        uid = str(interaction.user.id)

        trade["offers"][uid]["gold"] = amount
        trade["confirmed"][trade["user1"]] = False
        trade["confirmed"][trade["user2"]] = False
        trade["can_confirm_at"] = (datetime.utcnow() + timedelta(seconds=3)).isoformat()

        await interaction.response.send_message("Gold added.", ephemeral=True)
        await self.cog.refresh_trade_message(self.trade_id)


async def setup(bot):
    await bot.add_cog(Trades(bot))