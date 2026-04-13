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
                'cards': []
                }
            self.save_users()
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
                'cards': []
            }
            self.save_users()
        return self.users[uid]


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

        await ctx.send(embed=embed)

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

async def setup(bot):
    await bot.add_cog(Users(bot))