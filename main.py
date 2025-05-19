
```python
import os
import discord
from discord.ext import commands

# Utility to load/save JSON data
import json

def load_json(filepath):
    try:
        with open(filepath, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        return {}

def save_json(filepath, data):
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=4)

# Set up intents and bot instance
tokens = os.getenv('BOT_TOKEN')
intents = discord.Intents.default()
intents.members = True  # needed for on_member_join
bot = commands.Bot(command_prefix='!', intents=intents)

@bot.event
async def on_ready():
    print(f"Bot logged in as {bot.user}")

# Dynamically load all Cogs
initial_extensions = [
    'cogs.user_manager',
    'cogs.card_manager',
    'cogs.auction',
    'cogs.trade'
]

if __name__ == '__main__':
    for ext in initial_extensions:
        bot.load_extension(ext)
    bot.run(tokens)