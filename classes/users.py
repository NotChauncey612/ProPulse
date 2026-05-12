import discord
from discord.ext import commands
import math
import random
from datetime import datetime, timedelta, timezone

from .storage import load_json, save_json

DATA_PATH = 'data/users.json'
PRACTICE_COOLDOWN = timedelta(minutes=10)
DAILY_COOLDOWN = timedelta(hours=24)
RANKED_COOLDOWN = timedelta(minutes=30)
PROFILE_EMOJI = "👤"
COOLDOWN_EMOJI = "⏱️"
PRACTICE_EMOJI = "🏋️"
DAILY_EMOJI = "🎁"
CASH_EMOJI = "💵"
GOLD_EMOJI = "🟡"
CARDS_EMOJI = "🃏"
READY_EMOJI = "✅"
WAIT_EMOJI = "⏳"
ALERT_EMOJI = "🔔"
DM_EMOJI = "📬"
CONFIRM_EMOJI = "✅"
PACK_EMOJI = "🎴"

RANKED_EMOJI = "⚔️"

PRACTICE_XP_MIN = 15
PRACTICE_XP_MAX = 35
DEFAULT_ELO = 1000
LEADERBOARD_PER_PAGE = 20
TEAM_STAT_ROLES = ["TOP", "JNG", "MID", "BOT", "SUP"]
BASE_TEAM_STAT = 10
EMPTY_TEAM_SLOT_POWER_MULTIPLIER = 0.5
STAT_GAIN_MIN = 2
STAT_GAIN_MAX = 5
RANK_THRESHOLDS = [
    ("Challenger", 2200),
    ("Champ", 1800),
    ("Diamond", 1500),
    ("Gold", 1200),
    ("Silver", 0),
]
RANK_CASH_MULTIPLIERS = {
    "Silver": 1.0,
    "Gold": 1.25,
    "Diamond": 1.5,
    "Champ": 1.75,
    "Challenger": 2.0,
}


class Users(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.users = self.load_users()
        self.reminder_tasks = {}

    async def cog_load(self):
        self.schedule_pending_ready_notifications()

    def cog_unload(self):
        for task in self.reminder_tasks.values():
            task.cancel()
        self.reminder_tasks.clear()

    def load_users(self):
        data = load_json(DATA_PATH, default={})
        return data if isinstance(data, dict) else {}

    def save_users(self):
        save_json(DATA_PATH, self.users)

    def get_profile(self, member: discord.Member):
        uid = str(member.id)
        if uid not in self.users:
            self.users[uid] = {
                'cash': 0,
                'gold': 0,
                'last_practice': None, 
                'last_daily': None,
                'last_ranked': None,
                'packs': [], 
                'cards': [],
                'team': {},
                'xp': 0,
                'level': 1,
                'team_stats': self.default_team_stats(),
                'team_stat_level': 1,
                'elo': DEFAULT_ELO,
                'ranked_wins': 0,
                'ranked_losses': 0,
                'discord_username': member.name,
                'settings': self.default_settings()
                }
            self.save_users()
        else:
            self.users[uid]["discord_username"] = member.name
            self.users[uid].setdefault("settings", self.default_settings())
            self.users[uid].setdefault("team", {})
            self.users[uid].setdefault("last_ranked", None)
            self.normalize_profile_progress(self.users[uid])
            self.normalize_profile_currency(self.users[uid])
            self.save_users()
        return self.users[uid]

    def get_profile_by_id(self, user_id):
        uid = str(user_id)
        if uid not in self.users:
            self.users[uid] = {
                'cash': 0,
                'gold': 0,
                'last_practice': None,
                'last_daily': None,
                'last_ranked': None,
                'packs': [],
                'cards': [],
                'team': {},
                'xp': 0,
                'level': 1,
                'team_stats': self.default_team_stats(),
                'team_stat_level': 1,
                'elo': DEFAULT_ELO,
                'ranked_wins': 0,
                'ranked_losses': 0,
                'discord_username': None,
                'settings': self.default_settings()
            }
            self.save_users()
        else:
            self.users[uid].setdefault("settings", self.default_settings())
            self.users[uid].setdefault("team", {})
            self.users[uid].setdefault("last_ranked", None)
            self.normalize_profile_progress(self.users[uid])
            self.normalize_profile_currency(self.users[uid])
            self.save_users()
        return self.users[uid]

    def normalize_profile_currency(self, profile):
        if "cash" not in profile:
            profile["cash"] = profile.pop("gold", 0)
        if "radianite" in profile:
            profile["gold"] = profile.pop("radianite")
        else:
            profile.setdefault("gold", 0)

    def xp_for_level(self, level):
        total = 0
        cost = 100
        for target_level in range(2, level + 1):
            total += cost
            cost += 50 * target_level
        return total

    def level_for_xp(self, xp):
        level = 1
        while self.xp_for_level(level + 1) <= xp:
            level += 1
        return level

    def xp_progress_for_level(self, xp, level):
        current_level_xp = self.xp_for_level(level)
        next_level_xp = self.xp_for_level(level + 1)
        return xp - current_level_xp, next_level_xp - current_level_xp

    def default_team_stats(self):
        return {role: BASE_TEAM_STAT for role in TEAM_STAT_ROLES}

    def normalize_team_stats(self, profile):
        stats = profile.get("team_stats")
        if not isinstance(stats, dict):
            stats = {}
            profile["team_stats"] = stats

        for role in TEAM_STAT_ROLES:
            stats.setdefault(role, BASE_TEAM_STAT)

        profile.setdefault("team_stat_level", 1)
        while int(profile["team_stat_level"]) < int(profile.get("level", 1)):
            self.apply_team_stat_level_up(profile)

    def apply_team_stat_level_up(self, profile):
        stats = profile.setdefault("team_stats", self.default_team_stats())
        gains = {}
        for role in TEAM_STAT_ROLES:
            stats.setdefault(role, BASE_TEAM_STAT)
            gain = random.randint(STAT_GAIN_MIN, STAT_GAIN_MAX)
            stats[role] += gain
            gains[role] = gain
        profile["team_stat_level"] = int(profile.get("team_stat_level", 1)) + 1
        return gains

    def normalize_profile_progress(self, profile):
        profile.setdefault("xp", 0)
        profile["level"] = self.level_for_xp(int(profile.get("xp", 0)))
        self.normalize_team_stats(profile)
        profile.setdefault("elo", DEFAULT_ELO)
        profile.setdefault("ranked_wins", 0)
        profile.setdefault("ranked_losses", 0)

    def add_xp(self, profile, amount):
        self.normalize_profile_progress(profile)
        old_level = profile["level"]
        profile["xp"] += amount
        profile["level"] = self.level_for_xp(profile["xp"])
        level_gains = []
        while int(profile.get("team_stat_level", 1)) < profile["level"]:
            level_gains.append(self.apply_team_stat_level_up(profile))
        return profile["level"] > old_level, level_gains

    def rank_for_elo(self, elo):
        for rank_name, threshold in RANK_THRESHOLDS:
            if elo >= threshold:
                return rank_name
        return "Silver"

    def daily_cash_reward(self, profile):
        rank = self.rank_for_elo(int(profile.get("elo", DEFAULT_ELO)))
        multiplier = RANK_CASH_MULTIPLIERS.get(rank, 1.0)
        base_reward = random.randint(20, 50)
        return base_reward, round(base_reward * multiplier), rank, multiplier

    def team_slot_has_card(self, profile, role):
        team = profile.get("team") if isinstance(profile.get("team"), dict) else {}
        instance_id = team.get(role)
        if not instance_id:
            return False

        cards = profile.get("cards") if isinstance(profile.get("cards"), list) else []
        return any(
            isinstance(card, dict) and card.get("instance_id") == instance_id
            for card in cards
        )

    def team_slot_power(self, stats, role, has_card):
        stat = int(stats.get(role, BASE_TEAM_STAT))
        if has_card:
            return stat
        return round(stat * EMPTY_TEAM_SLOT_POWER_MULTIPLIER)

    def total_power(self, profile):
        stats = profile.get("team_stats")
        if not isinstance(stats, dict):
            stats = {}
        return sum(
            self.team_slot_power(stats, role, self.team_slot_has_card(profile, role))
            for role in TEAM_STAT_ROLES
        )

    def leaderboard_name(self, user_id, profile):
        return (
            profile.get("ign")
            or profile.get("discord_username")
            or profile.get("username")
            or f"User {str(user_id)[-4:]}"
        )

    def get_leaderboard_entries(self):
        entries = []
        for user_id, profile in self.users.items():
            if not isinstance(profile, dict):
                continue

            elo = int(profile.get("elo", DEFAULT_ELO))
            total_power = self.total_power(profile)
            entries.append({
                "user_id": user_id,
                "ign": self.leaderboard_name(user_id, profile),
                "total_power": total_power,
                "elo": elo,
            })

        entries.sort(
            key=lambda entry: (
                -entry["elo"],
                -entry["total_power"],
                entry["ign"].casefold(),
                str(entry["user_id"]),
            )
        )
        return entries

    def default_settings(self):
        return {
            "alert_daily_practice": True,
            "dm_auction_notis": True,
            "confirm_auction_buy": True,
            "confirm_pack_buy": True
        }

    def add_pack_to_first_slot(self, profile, pack_id):
        profile.setdefault("packs", [])
        for i, existing in enumerate(profile["packs"]):
            if existing is None:
                profile["packs"][i] = pack_id
                return i
        profile["packs"].append(pack_id)
        return len(profile["packs"]) - 1

    def remove_pack_at_slot(self, profile, slot_index):
        packs = profile.get("packs", [])
        if slot_index < 0 or slot_index >= len(packs):
            return None

        pack_id = packs[slot_index]
        if pack_id is None:
            return None

        packs[slot_index] = None
        return pack_id

    def utc_now(self):
        return datetime.now(timezone.utc)

    def parse_saved_time(self, saved_time):
        if not saved_time:
            return None

        saved = datetime.fromisoformat(saved_time)
        if saved.tzinfo is None:
            return saved.replace(tzinfo=timezone.utc)
        return saved.astimezone(timezone.utc)

    def seconds_until(self, ready_at):
        return max(0, math.ceil((ready_at - self.utc_now()).total_seconds()))

    def format_duration(self, total_seconds):
        days, remainder = divmod(total_seconds, 86400)
        hours, remainder = divmod(remainder, 3600)
        minutes, seconds = divmod(remainder, 60)

        if days:
            return f"{days}d {hours}h"
        if hours:
            return f"{hours}h {minutes}m"
        return f"{minutes}m {seconds}s"

    def format_remaining(self, ready_at):
        total_seconds = self.seconds_until(ready_at)
        return self.format_duration(total_seconds)

    def format_daily_remaining(self, ready_at):
        return self.format_remaining(ready_at)

    def remember_reminder_channel(self, user, action, channel_id):
        user[f"{action}_reminder_channel_id"] = channel_id

    def format_action_name(self, action):
        action_emojis = {
            "practice": PRACTICE_EMOJI,
            "daily": DAILY_EMOJI,
            "ranked": RANKED_EMOJI,
        }
        return f"{action_emojis.get(action, '')} {action}".strip()

    def schedule_ready_notification(self, channel_id: int, user_id: int, action: str, ready_at: datetime):
        task_key = (str(user_id), action)
        existing_task = self.reminder_tasks.get(task_key)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        task = self.bot.loop.create_task(self.notify_ready(channel_id, user_id, action, ready_at))
        self.reminder_tasks[task_key] = task

        def remove_finished_task(finished_task):
            if self.reminder_tasks.get(task_key) is finished_task:
                self.reminder_tasks.pop(task_key, None)

        task.add_done_callback(remove_finished_task)

    def cancel_ready_notifications(self, user_id, action=None):
        actions = [action] if action else ["practice", "daily", "ranked"]
        for action_name in actions:
            task = self.reminder_tasks.pop((str(user_id), action_name), None)
            if task and not task.done():
                task.cancel()

    def schedule_user_ready_notifications(self, user_id, user, now=None):
        now = now or self.utc_now()
        cooldowns = {
            "practice": PRACTICE_COOLDOWN,
            "daily": DAILY_COOLDOWN,
            "ranked": RANKED_COOLDOWN,
        }

        for action, cooldown in cooldowns.items():
            try:
                last_used = self.parse_saved_time(user.get(f"last_{action}"))
            except ValueError:
                continue

            channel_id = user.get(f"{action}_reminder_channel_id")
            if not last_used or not channel_id:
                continue

            ready_at = last_used + cooldown
            if ready_at <= now:
                continue

            try:
                self.schedule_ready_notification(int(channel_id), int(user_id), action, ready_at)
            except (TypeError, ValueError):
                continue

    def schedule_pending_ready_notifications(self):
        now = self.utc_now()

        for user_id, user in self.users.items():
            settings = user.get("settings", self.default_settings())
            if not settings.get("alert_daily_practice"):
                continue

            self.schedule_user_ready_notifications(user_id, user, now)


    # Commands 

    # Help command to list available commands and filters
    @commands.command()
    async def help(self, ctx):
        await ctx.send(
            "**Available Commands**\n\n"
            "**User**\n"
            "`.help` - Show this command list.\n"
            "`.profile [@user]` - View a profile. Your own profile includes settings buttons.\n"
            "`.cd` - Check practice, daily, and ranked cooldowns.\n"
            "`.practice` - Earn cash every 10 minutes.\n"
            "`.daily` - Earn cash and gold every 24 hours.\n"
            "`.leaderboard` or `.lb` - View users ranked by ELO.\n\n"
            "**Collection**\n"
            "`.inventory [filters]` or `.inv [filters]` - View your cards.\n"
            "Inventory filters: `-player <name/id>`, `-team <team>`, `-rarity <rarity>`, `-set <set>`, `-league <league>`, `-role <role>`.\n"
            "Example: `.inv -team T1 -rarity Gold -role mid`\n"
            "`.progress [filters]` - View collection progress and best rarity for each card.\n"
            "`.info [filters]` - List all bot cards. Supports inventory filters plus `-region <league>`.\n"
            "`.info <CID>` - View one card's image and pulled rarity counts.\n"
            "`.view <inventory #>` - View one card from your inventory.\n"
            "`.team` - View your lineup and set TOP/JNG/MID/BOT/SUP cards.\n"
            "`.ranked` - Battle a similar-ELO user's team for ELO.\n"
            "`.packs` - View your unopened packs.\n"
            "`.open <pack #|pack id|pack name>` - Open a pack.\n"
            "`.give @user <CID|card id> [-rarity <rarity>]` - Admin: grant a specific card.\n\n"
            "**Economy**\n"
            "`.shop` - View packs for sale and buy packs.\n"
            "`.auction` - View auctions with filter dropdowns, then select one to bid or buy now.\n"
            "`.auction -sell <inventory #>` - Auction one of your cards for 1-7 days.\n"
            "`.auction -sellpack <pack #>` - Auction one of your packs for 1-7 days.\n"
            "Use the auction buttons and dropdowns to filter, sort, view your listings, bid, buy now, or take down your own auction if nobody has bid yet.\n"
            "`.trade @user` - Start a trade with another user."
        )

    # Profile command to show user's balance and cards owned
    @commands.command()
    async def profile(self, ctx, member: discord.Member = None):
        member = member or ctx.author
        profile = self.get_profile(member)

        embed = discord.Embed(
            title=f"{PROFILE_EMOJI} {member.display_name}'s Profile"
        )

        embed.set_thumbnail(url=member.display_avatar.url)

        embed.add_field(name=f"{CASH_EMOJI} Cash", value=str(profile["cash"]), inline=True)
        embed.add_field(name=f"{GOLD_EMOJI} Gold", value=str(profile["gold"]), inline=True)
        embed.add_field(name=f"{CARDS_EMOJI} Cards Owned", value=str(len(profile["cards"])), inline=True)
        current_xp, needed_xp = self.xp_progress_for_level(profile["xp"], profile["level"])
        embed.add_field(name="Level", value=str(profile["level"]), inline=True)
        embed.add_field(name="XP", value=f"{current_xp}/{needed_xp}", inline=True)
        embed.add_field(name="ELO", value=str(profile["elo"]), inline=True)
        settings = profile.get("settings", self.default_settings())
        embed.add_field(
            name="Alerts/Confirmations",
            value=(
                f"{ALERT_EMOJI} Cooldown Alerts: {'ON' if settings['alert_daily_practice'] else 'OFF'}\n"
                f"{DM_EMOJI} Auction DMs: {'ON' if settings['dm_auction_notis'] else 'OFF'}\n"
                f"{CONFIRM_EMOJI} Auction Confirm Buy: {'ON' if settings['confirm_auction_buy'] else 'OFF'}\n"
                f"{PACK_EMOJI} Shop Confirm Buy: {'ON' if settings['confirm_pack_buy'] else 'OFF'}"
            ),
            inline=False
        )

        view = ProfileSettingsView(self, ctx.author.id, str(member.id)) if member.id == ctx.author.id else None
        await ctx.send(embed=embed, view=view)

    @commands.command(aliases=["lb"])
    async def leaderboard(self, ctx):
        entries = self.get_leaderboard_entries()
        view = LeaderboardView(self, entries)
        await ctx.send(embed=view.build_embed(), view=view)

    # CD (cooldown) command to show how long until user can practice or claim daily again
    @commands.command()
    async def cd(self, ctx):
        user = self.get_profile(ctx.author)
        now = self.utc_now()

        practice_cd = f"{READY_EMOJI} Ready"
        daily_cd = f"{READY_EMOJI} Ready"
        ranked_cd = f"{READY_EMOJI} Ready"

        last_practice = self.parse_saved_time(user.get("last_practice"))
        if last_practice:
            practice_ready_at = last_practice + PRACTICE_COOLDOWN
            if now < practice_ready_at:
                practice_cd = self.format_remaining(practice_ready_at)

        last_daily = self.parse_saved_time(user.get("last_daily"))
        if last_daily:
            daily_ready_at = last_daily + DAILY_COOLDOWN
            if now < daily_ready_at:
                daily_cd = self.format_daily_remaining(daily_ready_at)

        last_ranked = self.parse_saved_time(user.get("last_ranked"))
        if last_ranked:
            ranked_ready_at = last_ranked + RANKED_COOLDOWN
            if now < ranked_ready_at:
                ranked_cd = self.format_remaining(ranked_ready_at)

        embed = discord.Embed(title=f"{COOLDOWN_EMOJI} {ctx.author.display_name}'s Cooldowns")
        embed.add_field(name=f"{PRACTICE_EMOJI} Practice", value=practice_cd, inline=True)
        embed.add_field(name=f"{DAILY_EMOJI} Daily", value=daily_cd, inline=True)
        embed.add_field(name=f"{RANKED_EMOJI} Ranked", value=ranked_cd, inline=True)

        await ctx.send(embed=embed)


    # Practice command to earn cash every 10 minutes
    @commands.command()
    async def practice(self, ctx):
        user = self.get_profile(ctx.author)
        now = self.utc_now()

        last_time = self.parse_saved_time(user.get("last_practice"))

        if last_time:
            practice_ready_at = last_time + PRACTICE_COOLDOWN
            if now < practice_ready_at:
                await ctx.send(f"{WAIT_EMOJI} Wait {self.format_remaining(practice_ready_at)} before practicing again.")
                return

        reward = random.randint(5, 10)
        xp_reward = random.randint(PRACTICE_XP_MIN, PRACTICE_XP_MAX)
        leveled_up, stat_gains = self.add_xp(user, xp_reward)
        user["cash"] += reward
        user["last_practice"] = now.isoformat()
        self.remember_reminder_channel(user, "practice", ctx.channel.id)

        self.save_users()

        level_text = ""
        if leveled_up:
            latest_gains = stat_gains[-1] if stat_gains else {}
            gain_text = ", ".join(f"{role} +{gain}" for role, gain in latest_gains.items())
            level_text = f" You leveled up to **Level {user['level']}**."
            if gain_text:
                level_text += f" Stat gains: {gain_text}."
        await ctx.send(
            f"{PRACTICE_EMOJI} {ctx.author.mention} You practiced and earned "
            f"{reward} {CASH_EMOJI} cash and {xp_reward} XP.{level_text}"
        )
        if user.get("settings", self.default_settings()).get("alert_daily_practice"):
            ready_at = now + PRACTICE_COOLDOWN
            self.schedule_ready_notification(ctx.channel.id, ctx.author.id, "practice", ready_at)

    # Daily command to earn cash and gold once every 24 hours
    @commands.command()
    async def daily(self, ctx):
        user = self.get_profile(ctx.author)
        now = self.utc_now()

        last_time = self.parse_saved_time(user.get("last_daily"))

        if last_time:
            daily_ready_at = last_time + DAILY_COOLDOWN
            if now < daily_ready_at:
                await ctx.send(f"{WAIT_EMOJI} Wait {self.format_daily_remaining(daily_ready_at)} before claiming daily again.")
                return

        base_reward, reward, rank, multiplier = self.daily_cash_reward(user)
        user["cash"] += reward
        user["gold"] += 5
        user["last_daily"] = now.isoformat()
        self.remember_reminder_channel(user, "daily", ctx.channel.id)

        self.save_users()

        multiplier_text = ""
        if multiplier > 1:
            multiplier_text = f" ({base_reward} x {multiplier:g} {rank} multiplier)"
        await ctx.send(
            f"{DAILY_EMOJI} {ctx.author.mention} You claimed your daily reward and earned "
            f"{reward} {CASH_EMOJI} cash{multiplier_text} and 5 {GOLD_EMOJI} gold."
        )
        if user.get("settings", self.default_settings()).get("alert_daily_practice"):
            ready_at = now + DAILY_COOLDOWN
            self.schedule_ready_notification(ctx.channel.id, ctx.author.id, "daily", ready_at)

    async def notify_ready(self, channel_id: int, user_id: int, action: str, ready_at: datetime):
        if ready_at.tzinfo is None:
            ready_at = ready_at.replace(tzinfo=timezone.utc)
        await self.bot.wait_until_ready()
        await discord.utils.sleep_until(ready_at.astimezone(timezone.utc))
        try:
            channel = self.bot.get_channel(channel_id)
            if channel is None:
                channel = await self.bot.fetch_channel(channel_id)
            if channel:
                await channel.send(f"{READY_EMOJI} <@{user_id}> your **{self.format_action_name(action)}** is ready.")
        except Exception:
            pass


class LeaderboardView(discord.ui.View):
    def __init__(self, users_cog: Users, entries):
        super().__init__(timeout=120)
        self.users_cog = users_cog
        self.entries = entries
        self.page = 0
        self.update_buttons()

    def total_pages(self):
        if not self.entries:
            return 1
        return (len(self.entries) - 1) // LEADERBOARD_PER_PAGE + 1

    def page_start(self):
        return self.page * LEADERBOARD_PER_PAGE

    def get_page_entries(self):
        start = self.page_start()
        return self.entries[start:start + LEADERBOARD_PER_PAGE]

    def build_embed(self):
        lines = []
        for index, entry in enumerate(self.get_page_entries(), start=self.page_start() + 1):
            lines.append(
                f"`#{index:>2}` **{entry['ign']}** | "
                f"Power: `{entry['total_power']}` | ELO: `{entry['elo']}`"
            )

        description = "\n".join(lines) if lines else "No users are on the leaderboard yet."
        embed = discord.Embed(
            title="Leaderboard",
            description=description,
            color=discord.Color.dark_grey()
        )
        embed.set_footer(
            text=f"Page {self.page + 1}/{self.total_pages()} - {len(self.entries)} players"
        )
        return embed

    def update_buttons(self):
        self.previous_button.disabled = self.page <= 0
        self.next_button.disabled = self.page >= self.total_pages() - 1

    @discord.ui.button(label="<", style=discord.ButtonStyle.secondary)
    async def previous_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page -= 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label=">", style=discord.ButtonStyle.secondary)
    async def next_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page += 1
        self.update_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class ProfileSettingsView(discord.ui.View):
    BUTTON_SETTINGS = {
        "profile_settings:alert_daily_practice": "alert_daily_practice",
        "profile_settings:dm_auction_notis": "dm_auction_notis",
        "profile_settings:confirm_auction_buy": "confirm_auction_buy",
        "profile_settings:confirm_pack_buy": "confirm_pack_buy",
    }

    def __init__(self, users_cog: Users, requester_id: int, target_uid: str):
        super().__init__(timeout=180)
        self.users_cog = users_cog
        self.requester_id = requester_id
        self.target_uid = target_uid
        self.refresh_button_styles()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("You can only edit your own settings.", ephemeral=True)
            return False
        return True

    def toggle(self, key):
        profile = self.users_cog.get_profile_by_id(self.target_uid)
        settings = profile.setdefault("settings", self.users_cog.default_settings())
        settings[key] = not settings.get(key, True)
        if key == "alert_daily_practice":
            if settings[key]:
                self.users_cog.schedule_user_ready_notifications(self.target_uid, profile)
            else:
                self.users_cog.cancel_ready_notifications(self.target_uid)
        self.users_cog.save_users()
        self.refresh_button_styles()
        return settings[key]

    def get_settings(self):
        profile = self.users_cog.get_profile_by_id(self.target_uid)
        return profile.setdefault("settings", self.users_cog.default_settings())

    def refresh_button_styles(self):
        settings = self.get_settings()
        for item in self.children:
            key = self.BUTTON_SETTINGS.get(getattr(item, "custom_id", None))
            if key:
                item.style = discord.ButtonStyle.success if settings.get(key, True) else discord.ButtonStyle.danger

    async def send_toggle_response(self, interaction: discord.Interaction, message: str):
        await interaction.response.send_message(message, ephemeral=True)
        await interaction.message.edit(view=self)

    @discord.ui.button(label=f"{ALERT_EMOJI} Toggle Cooldown Alerts", style=discord.ButtonStyle.secondary, custom_id="profile_settings:alert_daily_practice")
    async def toggle_alerts(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("alert_daily_practice")
        await self.send_toggle_response(interaction, f"Cooldown alerts are now {'ON' if state else 'OFF'}.")

    @discord.ui.button(label=f"{DM_EMOJI} Toggle Auction DMs", style=discord.ButtonStyle.secondary, custom_id="profile_settings:dm_auction_notis")
    async def toggle_auction_dms(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("dm_auction_notis")
        await self.send_toggle_response(interaction, f"Auction DMs are now {'ON' if state else 'OFF'}.")

    @discord.ui.button(label=f"{CONFIRM_EMOJI} Toggle Auction Confirm", style=discord.ButtonStyle.secondary, custom_id="profile_settings:confirm_auction_buy")
    async def toggle_auction_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("confirm_auction_buy")
        await self.send_toggle_response(interaction, f"Auction purchase confirmation is now {'ON' if state else 'OFF'}.")

    @discord.ui.button(label=f"{PACK_EMOJI} Toggle Pack Confirm", style=discord.ButtonStyle.secondary, custom_id="profile_settings:confirm_pack_buy")
    async def toggle_pack_confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("confirm_pack_buy")
        await self.send_toggle_response(interaction, f"Pack purchase confirmation is now {'ON' if state else 'OFF'}.")

async def setup(bot):
    await bot.add_cog(Users(bot))
