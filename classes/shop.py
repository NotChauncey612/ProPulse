import json
import discord
from discord.ext import commands

PACKS_PATH = "data/packs.json"


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


class ConfirmPurchaseView(discord.ui.View):
    def __init__(self, pack_data: dict):
        super().__init__(timeout=30)
        self.pack_data = pack_data

    @discord.ui.button(label="Confirm Purchase", style=discord.ButtonStyle.green)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        user = interaction.user
        price = self.pack_data.get("price", 0)
        pack_id = self.pack_data.get("pack_id")
        pack_name = self.pack_data.get("name", "Unknown Pack")

        users_cog = interaction.client.get_cog("Users")
        if users_cog is None:
            await interaction.response.send_message(
                "Users system is not loaded.", ephemeral=True
            )
            return

        profile = users_cog.get_profile(user)

        if profile["gold"] < price:
            await interaction.response.send_message(
                "❌ You don't have enough gold to purchase that.",
                ephemeral=True
            )
            return

        profile["gold"] -= price
        profile.setdefault("packs", [])
        profile["packs"].append(pack_id)
        users_cog.save_users()

        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Purchase Successful",
                description=(
                    f"You bought **{pack_name}**\n\n"
                    f"💰 -{price} gold\n"
                    f"💼 Pack added to your inventory"
                ),
                color=discord.Color.green()
            ),
            view=None
        )

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red)
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
                        f"{pack.get('cards_per_pack', 0)} cards • "
                        f"{pack.get('price', 0)} gold"
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
            if profile["gold"] < price:
                await interaction.response.send_message("❌ You don't have enough gold to purchase that.", ephemeral=True)
                return
            profile["gold"] -= price
            profile.setdefault("packs", [])
            profile["packs"].append(pack_data.get("pack_id"))
            users_cog.save_users()
            await interaction.response.send_message(
                f"✅ Purchased **{pack_data.get('name', 'Unknown Pack')}** for {price} gold.",
                ephemeral=True
            )
            return

        embed = discord.Embed(
            title="Confirm Purchase",
            description=(
                f"Are you sure you want to buy **{pack_data.get('name', 'Unknown Pack')}**?\n\n"
                f"Game: {pack_data.get('game', 'Unknown')}\n"
                f"Set: {pack_data.get('set', 'Unknown')}\n"
                f"Cards: {pack_data.get('cards_per_pack', 0)}\n"
                f"Cost: {pack_data.get('price', 0)} gold"
            ),
            color=discord.Color.orange()
        )

        view = ConfirmPurchaseView(pack_data)
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
            title="🛒 Card Shop",
            description="Select a pack below to purchase.",
            color=discord.Color.blue()
        )

        if not packs:
            embed.description = "No packs are currently available."
            return embed

        for pack in packs:
            league_text = pack.get("league")
            if not league_text and pack.get("leagues"):
                league_text = ", ".join(pack["leagues"])

            details = [
                f"Game: {pack.get('game', 'Unknown')}",
                f"Set: {pack.get('set', 'Unknown')}",
                f"Cards: {pack.get('cards_per_pack', 0)}",
                f"Cost: {pack.get('price', 0)} gold"
            ]
            if league_text:
                details.insert(2, f"League: {league_text}")

            embed.add_field(
                name=pack.get("name", "Unknown Pack"),
                value="\n".join(details),
                inline=False
            )

        return embed

    @commands.command()
    async def shop(self, ctx):
        packs = load_packs()
        embed = self.create_embed(packs)
        view = ShopView(packs)
        await ctx.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(Shop(bot))