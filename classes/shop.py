import discord
from discord.ext import commands

from .storage import load_json

PACKS_PATH = "data/packs.json"
CASH_EMOJI = "💵"


def load_packs():
    data = load_json(PACKS_PATH, default={})

    if isinstance(data, dict) and "packs" in data:
        packs = data["packs"]
    elif isinstance(data, list):
        packs = data
    else:
        packs = []

    return [pack for pack in packs if isinstance(pack, dict)]


class QuantityModal(discord.ui.Modal, title="Pack Quantity"):
    quantity = discord.ui.TextInput(
        label="Quantity",
        placeholder="Enter number of packs",
        default="1",
        min_length=1,
        max_length=6,
    )

    def __init__(self, purchase_view):
        super().__init__(timeout=60)
        self.purchase_view = purchase_view
        self.quantity.default = str(purchase_view.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        raw_quantity = str(self.quantity.value).strip()
        if not raw_quantity.isdigit():
            await interaction.response.send_message(
                "Please enter a whole number of packs.", ephemeral=True
            )
            return

        quantity = int(raw_quantity)
        if quantity < 1:
            await interaction.response.send_message(
                "Please enter at least 1 pack.", ephemeral=True
            )
            return

        total_price = self.purchase_view.price * quantity
        users_cog = interaction.client.get_cog("Users")
        profile = users_cog.get_profile(interaction.user) if users_cog else None
        if profile and profile["cash"] < total_price:
            await interaction.response.send_message(
                embed=discord.Embed(
                    title="Not Enough Cash",
                    description=(
                        f"You need {CASH_EMOJI} {total_price} cash to buy "
                        f"{quantity} pack{'s' if quantity != 1 else ''}."
                    ),
                    color=discord.Color.red()
                ),
                ephemeral=True
            )
            return

        self.purchase_view.quantity = quantity
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

    @discord.ui.button(label="Enter Quantity", style=discord.ButtonStyle.blurple, row=0)
    async def enter_quantity(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(QuantityModal(self))

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
                embed=discord.Embed(
                    title="Not Enough Cash",
                    description=(
                        f"You need {CASH_EMOJI} {total_price} cash to buy "
                        f"{self.quantity} pack{'s' if self.quantity != 1 else ''}."
                    ),
                    color=discord.Color.red()
                ),
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
            options=options,
            row=0
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
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="Not Enough Cash",
                        description=f"You need {CASH_EMOJI} {price} cash to buy that pack.",
                        color=discord.Color.red()
                    ),
                    ephemeral=True
                )
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
    def __init__(self, shop_cog, packs: list[dict]):
        super().__init__(timeout=120)
        self.shop_cog = shop_cog
        self.packs = packs
        self.rebuild_items()

    def rebuild_items(self):
        self.clear_items()
        if self.packs:
            self.add_item(PackSelect(self.packs))

    def create_embed(self):
        return self.shop_cog.create_shop_embed(self.packs)


class Shop(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

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
            value_lines = [
                f"🎮 Game: {pack.get('game', 'Unknown')}",
                f"📦 Set: {pack.get('set', 'Unknown')}",
            ]

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
        view = ShopView(self, packs)
        await ctx.send(embed=view.create_embed(), view=view)


async def setup(bot):
    await bot.add_cog(Shop(bot))
