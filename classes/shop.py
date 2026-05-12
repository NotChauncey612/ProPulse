import json
import discord
from discord.ext import commands

PACKS_PATH = "data/packs.json"
CASH_EMOJI = "💵"


def load_packs():
    with open(PACKS_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, dict) and "packs" in data:
        packs = data["packs"]
    elif isinstance(data, list):
        packs = data
    else:
        packs = []

    return [pack for pack in packs if isinstance(pack, dict)]


class QuantitySelect(discord.ui.Select):
    def __init__(self, purchase_view):
        self.purchase_view = purchase_view
        options = [
            discord.SelectOption(
                label=f"{quantity} pack{'s' if quantity != 1 else ''}",
                description=(
                    f"🃏 {quantity * purchase_view.cards_per_pack} cards • "
                    f"{CASH_EMOJI} {quantity * purchase_view.price} cash"
                )[:100],
                value=str(quantity),
                default=quantity == purchase_view.quantity
            )
            for quantity in (1, 2, 3, 5, 10)
        ]

        super().__init__(
            placeholder="Choose quantity...",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        self.purchase_view.quantity = int(self.values[0])
        for option in self.options:
            option.default = option.value == self.values[0]

        await interaction.response.edit_message(
            embed=self.purchase_view.create_embed(),
            view=self.purchase_view
        )


class ConfirmPurchaseView(discord.ui.View):
    def __init__(self, pack_data: dict):
        super().__init__(timeout=60)
        self.pack_data = pack_data
        self.quantity = 1
        self.price = pack_data.get("price", 0)
        self.cards_per_pack = pack_data.get("cards_per_pack", 0)
        self.add_item(QuantitySelect(self))

    def total_price(self):
        return self.price * self.quantity

    def create_embed(self):
        pack_name = self.pack_data.get("name", "Unknown Pack")
        total_cards = self.cards_per_pack * self.quantity

        return discord.Embed(
            title="🛒 Confirm Purchase",
            description=(
                f"📦 **Pack:** {pack_name}\n"
                f"🔢 **Quantity:** {self.quantity}\n"
                f"🃏 **Cards:** {self.cards_per_pack} per pack ({total_cards} total)\n"
                f"{CASH_EMOJI} **Cost:** {self.total_price()} cash"
            ),
            color=discord.Color.orange()
        )

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.green, row=1)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        total_price = self.total_price()
        pack_id = self.pack_data.get("pack_id")
        pack_name = self.pack_data.get("name", "Unknown Pack")

        users_cog = interaction.client.get_cog("Users")
        if users_cog is None:
            await interaction.response.send_message(
                "Users system is not loaded.", ephemeral=True
            )
            return

        profile = users_cog.get_profile(user)

        if profile["cash"] < total_price:
            await interaction.response.send_message(
                f"❌ You need {total_price} cash to buy that many packs.",
                ephemeral=True
            )
            return

        profile["cash"] -= total_price
        for _ in range(self.quantity):
            users_cog.add_pack_to_first_slot(profile, pack_id)
        users_cog.save_users()

        pack_word = "pack" if self.quantity == 1 else "packs"
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="✅ Purchase Successful",
                description=(
                    f"You bought **{self.quantity}x {pack_name}**\n\n"
                    f"{CASH_EMOJI} -{total_price} cash\n"
                    f"📦 {self.quantity} {pack_word} added to your inventory"
                ),
                color=discord.Color.green()
            ),
            view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, row=1)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="❌ Purchase cancelled.",
            embed=None,
            view=None
        )


class PackSelect(discord.ui.Select):
    def __init__(self, packs: list[dict]):
        self.packs_by_id = {
            pack["pack_id"]: pack
            for pack in packs
            if pack.get("pack_id")
        }

        options = []
        for pack in self.packs_by_id.values():
            options.append(
                discord.SelectOption(
                    label=pack.get("name", "Unknown Pack")[:100],
                    description=(
                        f"🃏 {pack.get('cards_per_pack', 0)} cards • "
                        f"{CASH_EMOJI} {pack.get('price', 0)} cash"
                    )[:100],
                    value=pack["pack_id"]
                )
            )

        super().__init__(
            placeholder="Choose a pack...",
            min_values=1,
            max_values=1,
            options=options
        )

    async def callback(self, interaction: discord.Interaction):
        selected_pack_id = self.values[0]
        pack_data = self.packs_by_id[selected_pack_id]
        users_cog = interaction.client.get_cog("Users")
        profile = users_cog.get_profile(interaction.user) if users_cog else None
        settings = profile.get("settings", {}) if profile else {}
        should_confirm = settings.get("confirm_pack_buy", True)

        if not should_confirm and users_cog:
            price = pack_data.get("price", 0)
            if profile["cash"] < price:
                await interaction.response.send_message("❌ You don't have enough cash to purchase that.", ephemeral=True)
                return
            profile["cash"] -= price
            users_cog.add_pack_to_first_slot(profile, pack_data.get("pack_id"))
            users_cog.save_users()
            await interaction.response.send_message(
                f"✅ Purchased **{pack_data.get('name', 'Unknown Pack')}** for {CASH_EMOJI} {price} cash.",
                ephemeral=True
            )
            return

        view = ConfirmPurchaseView(pack_data)
        embed = view.create_embed()
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


class ShopView(discord.ui.View):
    def __init__(self, packs: list[dict]):
        super().__init__(timeout=120)
        if packs:
            self.add_item(PackSelect(packs))


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    def create_embed(self, packs: list[dict]) -> discord.Embed:
        embed = discord.Embed(
            title="Card Shop",
            description="Select a pack below to purchase.",
            color=discord.Color.blue()
        )

        for index, pack in enumerate(packs):
            league_text = pack.get("league")

        if not league_text and pack.get("leagues"):
            league_text = ", ".join(pack["leagues"])

        value_lines = [
            f"🎮 **Game:** {pack.get('game', 'Unknown')}",
            f"📦 **Set:** {pack.get('set', 'Unknown')}",
        ]

        if league_text:
            value_lines.append(f"🏆 **League:** {league_text}")

        value_lines.extend([
            f"🃏 **Cards:** {pack.get('cards_per_pack', 0)}",
            f"{CASH_EMOJI} **Cost:** {pack.get('price', 0)} cash"
        ])

        embed.add_field(
            name=pack.get("name", "Unknown Pack"),
            value="\n".join(value_lines),
            inline=False
        )

        return embed

    def create_shop_embed(self, packs: list[dict]) -> discord.Embed:
        embed = discord.Embed(
            title="🛒 Card Shop",
            description="Select a pack below to purchase.",
            color=discord.Color.blue()
        )

        if not packs:
            embed.description = "No packs are available right now."
            return embed

        for pack in packs:
            league_text = pack.get("league")
            if not league_text and pack.get("leagues"):
                league_text = ", ".join(pack["leagues"])

            value_lines = [
                f"🎮 Game: {pack.get('game', 'Unknown')}",
                f"📦 Set: {pack.get('set', 'Unknown')}",
            ]

            if league_text:
                value_lines.append(f"🏆 League: {league_text}")

            value_lines.extend([
                f"🃏 Cards: {pack.get('cards_per_pack', 0)}",
                f"{CASH_EMOJI} Cost: {pack.get('price', 0)} cash"
            ])

            embed.add_field(
                name=f"📦 {pack.get('name', 'Unknown Pack')}",
                value=f"```\n{chr(10).join(value_lines)}\n```",
                inline=True
            )

        row_remainder = len(packs) % 3
        if row_remainder:
            for _ in range(3 - row_remainder):
                embed.add_field(name="\u200b", value="\u200b", inline=True)

        return embed

    @commands.command()
    async def shop(self, ctx):
        packs = load_packs()
        embed = self.create_shop_embed(packs)
        view = ShopView(packs)
        await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Shop(bot))
