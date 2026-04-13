import discord
from discord.ext import commands
import json
import os
import random
from datetime import datetime, timedelta

DATA_PATH = 'data/users.json'

class Users(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.users = self.load_users()

    def load_users(self):
        try:
            with open(DATA_PATH, 'r') as f:
                return json.load(f)
        except FileNotFoundError:
            return {}

    def save_users(self):
        os.makedirs(os.path.dirname(DATA_PATH), exist_ok=True)
        with open(DATA_PATH, 'w') as f:
            json.dump(self.users, f, indent=4)

    def get_profile(self, member: discord.Member):
        uid = str(member.id)
        if uid not in self.users:
            self.users[uid] = {
                'gold': 0,
                'radianite': 0, 
                'last_practice': None, 
                'last_daily': None, 
                'packs': [], 
                'cards': [],
                'discord_username': member.name,
                'settings': self.default_settings()
                }
            self.save_users()
        else:
            self.users[uid]["discord_username"] = member.name
            self.users[uid].setdefault("settings", self.default_settings())
        return self.users[uid]

    def get_profile_by_id(self, user_id):
        uid = str(user_id)
        if uid not in self.users:
            self.users[uid] = {
                'gold': 0,
                'radianite': 0,
                'last_practice': None,
                'last_daily': None,
                'packs': [],
                'cards': [],
                'discord_username': None,
                'settings': self.default_settings()
            }
            self.save_users()
        else:
            self.users[uid].setdefault("settings", self.default_settings())
        return self.users[uid]

    def default_settings(self):
        return {
            "alert_daily_practice": True,
            "dm_auction_notis": True,
            "confirm_auction_buy": True,
            "confirm_pack_buy": True
        }


    # Commands 

    # Join command to create a profile for the user
    @commands.command()
    async def join(self, ctx):
        profile = self.get_profile(ctx.author)
        await ctx.send(f"Welcome, **{ctx.author.display_name}**! Your profile has been created.")

    # Profile command to show user's balance and cards owned
    @commands.command()
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        profile = self.get_profile(member)

        embed = discord.Embed(
            title=f"{member.display_name}'s Profile"
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name="Gold", value=str(profile["gold"]), inline=True)
        embed.add_field(name="Radianite", value=str(profile["radianite"]), inline=True)
        embed.add_field(name="Cards Owned", value=str(len(profile["cards"])), inline=True)
        settings = profile.get("settings", self.default_settings())
        embed.add_field(
            name="Alerts/Confirmations",
            value=(
                f"Practice/Daily Alerts: {'ON' if settings['alert_daily_practice'] else 'OFF'}\n"
                f"Auction DMs: {'ON' if settings['dm_auction_notis'] else 'OFF'}\n"
                f"Auction Confirm Buy: {'ON' if settings['confirm_auction_buy'] else 'OFF'}\n"
                f"Pack Confirm Buy: {'ON' if settings['confirm_pack_buy'] else 'OFF'}"
            ),
            inline=False
        )

        await ctx.send(embed=embed, view=ProfileSettingsView(self, ctx.author.id, str(member.id)))

    # CD (cooldown) command to show how long until user can practice or claim daily again
    @commands.command()
    async def cd(self, ctx):
        user = self.get_profile(ctx.author)
        now = datetime.utcnow()

        practice_cd = "Ready"
        daily_cd = "Ready"

        if user.get("last_practice"):
            last_practice = datetime.fromisoformat(user["last_practice"])
            if now < last_practice + timedelta(minutes=10):
                remaining = (last_practice + timedelta(minutes=10)) - now
                minutes, seconds = divmod(int(remaining.total_seconds()), 60)
                practice_cd = f"{minutes}m {seconds}s"

        if user.get("last_daily"):
            last_daily = datetime.fromisoformat(user["last_daily"])
            if now < last_daily + timedelta(hours=24):
                remaining = (last_daily + timedelta(hours=24)) - now
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                daily_cd = f"{hours}h {minutes}m {seconds}s"

        embed = discord.Embed(title=f"{ctx.author.display_name}'s Cooldowns")
        embed.add_field(name="Practice", value=practice_cd, inline=True)
        embed.add_field(name="Daily", value=daily_cd, inline=True)

        await ctx.send(embed=embed)


    # Practice command to earn gold every 10 minutes
    @commands.command()
    async def practice(self, ctx):
        user = self.get_profile(ctx.author)
        now = datetime.utcnow()

        last_time = user.get("last_practice")

        if last_time:
            last_time = datetime.fromisoformat(last_time)
            if now < last_time + timedelta(minutes=10):
                remaining = (last_time + timedelta(minutes=10)) - now
                minutes, seconds = divmod(int(remaining.total_seconds()), 60)
                await ctx.send(f"Wait {minutes}m {seconds}s before practicing again.")
                return

        reward = random.randint(5, 10)
        user["gold"] += reward
        user["last_practice"] = now.isoformat()

        self.save_users()

        await ctx.send(f"You practiced and earned {reward} gold.")
        if user.get("settings", self.default_settings()).get("alert_daily_practice"):
            self.bot.loop.create_task(self.notify_ready(ctx.author, "practice", 10 * 60))

    # Daily command to earn gold once every 24 hours
    @commands.command()
    async def daily(self, ctx):
        user = self.get_profile(ctx.author)
        now = datetime.utcnow()

        last_time = user.get("last_daily")

        if last_time:
            last_time = datetime.fromisoformat(last_time)
            if now < last_time + timedelta(hours=24):
                remaining = (last_time + timedelta(hours=24)) - now
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, seconds = divmod(remainder, 60)
                await ctx.send(f"Wait {hours}h {minutes}m {seconds}s before claiming daily again.")
                return

        reward = random.randint(20, 50)
        user["gold"] += reward
        user["radianite"] += 5
        user["last_daily"] = now.isoformat()

        self.save_users()

        await ctx.send(f"You claimed your daily reward and earned {reward} gold and 5 radianite.")
        if user.get("settings", self.default_settings()).get("alert_daily_practice"):
            self.bot.loop.create_task(self.notify_ready(ctx.author, "daily", 24 * 60 * 60))

    async def notify_ready(self, member: discord.Member, action: str, wait_seconds: int):
        await discord.utils.sleep_until(datetime.utcnow() + timedelta(seconds=wait_seconds))
        try:
            await member.send(f"🔔 Your **{action}** is ready again.")
        except Exception:
            pass


class ProfileSettingsView(discord.ui.View):
    def __init__(self, users_cog: Users, requester_id: int, target_uid: str):
        super().__init__(timeout=180)
        self.users_cog = users_cog
        self.requester_id = requester_id
        self.target_uid = target_uid

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("You can only edit your own settings.", ephemeral=True)
            return False
        return True

    def toggle(self, key):
        profile = self.users_cog.get_profile_by_id(self.target_uid)
        settings = profile.setdefault("settings", self.users_cog.default_settings())
        settings[key] = not settings.get(key, True)
        self.users_cog.save_users()
        return settings[key]

    @discord.ui.button(label="Toggle Daily/Practice Alerts", style=discord.ButtonStyle.secondary)
    async def toggle_alerts(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("alert_daily_practice")
        await interaction.response.send_message(f"Daily/Practice alerts are now {'ON' if state else 'OFF'}.", ephemeral=True)

    @discord.ui.button(label="Toggle Auction DMs", style=discord.ButtonStyle.secondary)
    async def toggle_auction_dms(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("dm_auction_notis")
        await interaction.response.send_message(f"Auction DMs are now {'ON' if state else 'OFF'}.", ephemeral=True)

    @discord.ui.button(label="Toggle Auction Confirm", style=discord.ButtonStyle.secondary)
    async def toggle_auction_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("confirm_auction_buy")
        await interaction.response.send_message(f"Auction purchase confirmation is now {'ON' if state else 'OFF'}.", ephemeral=True)

    @discord.ui.button(label="Toggle Pack Confirm", style=discord.ButtonStyle.secondary)
    async def toggle_pack_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("confirm_pack_buy")
        await interaction.response.send_message(f"Pack purchase confirmation is now {'ON' if state else 'OFF'}.", ephemeral=True)

async def setup(bot):
    await bot.add_cog(Users(bot))