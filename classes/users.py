import json, os
from discord.ext import commands

DATA_PATH = 'data/users.json'

class UserManager(commands.Cog):
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
        with open(DATA_PATH, 'w') as f:
            json.dump(self.users, f, indent=4)

    @commands.Cog.listener()
    async def on_member_join(self, member):
        uid = str(member.id)
        if uid not in self.users:
            self.users[uid] = {'balance': 0, 'cards': []}
            self.save_users()

    def get_profile(self, member):
        return self.users.setdefault(str(member.id), {'balance': 0, 'cards': []})

    @commands.command(name='profile')
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        profile = self.get_profile(member)
        await ctx.send(f"**{member.display_name}**\nBalance: {profile['balance']}\nCards owned: {len(profile['cards'])}")


def setup(bot):
    bot.add_cog(UserManager(bot))