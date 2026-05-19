import discord
from discord.ext import commands
import asyncio
import math
import os
import random
import tempfile
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from .i18n import available_languages, translator
from .storage import data_path, load_json, save_json

DATA_PATH = 'data/users.json'
MODERATORS_PATH = 'data/moderators.json'
PRACTICE_COOLDOWN = timedelta(minutes=10)
DAILY_COOLDOWN = timedelta(hours=24)
RANKED_COOLDOWN = timedelta(minutes=30)
VOTE_CASH_REWARD = 50
VOTE_GOLD_REWARD = 5
TOPGG_TOKEN = os.getenv("TOPGG_TOKEN")
TOPGG_BOT_ID = os.getenv("TOPGG_BOT_ID", "1370559950496993440")
MODERATOR_MANAGER_IDS = {
    user_id.strip()
    for user_id in os.getenv("MODERATOR_MANAGER_IDS", "").replace(";", ",").split(",")
    if user_id.strip().isdigit()
}
VOTE_EMOJI = "\U0001f5f3\ufe0f"
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
STARTING_CASH = 200
DEFAULT_ELO = 1000
LEADERBOARD_PER_PAGE = 20
TEAM_STAT_ROLES = ["TOP", "JNG", "MID", "BOT", "SUP"]
GAME_LEAGUE = "League of Legends"
GAME_VALORANT = "Valorant"
DEFAULT_TEAM_GAME = GAME_LEAGUE
VALORANT_TEAM_STAT_ROLES = ["S1", "S2", "S3", "S4", "S5"]
TEAM_STAT_ROLES_BY_GAME = {
    GAME_LEAGUE: TEAM_STAT_ROLES,
    GAME_VALORANT: VALORANT_TEAM_STAT_ROLES,
}
VALORANT_STAT_ROLE_MAP = dict(zip(VALORANT_TEAM_STAT_ROLES, TEAM_STAT_ROLES))
BASE_TEAM_STAT = 10
EMPTY_TEAM_SLOT_POWER_MULTIPLIER = 1.0
STAT_GAIN_MIN = 2
STAT_GAIN_MAX = 5
RANK_THRESHOLDS = [
    ("Challenger", 2200),
    ("Champ", 1800),
    ("Diamond", 1500),
    ("Gold", 1200),
    ("Silver", 0),
]
MAIN_DISCORD_INVITE = os.getenv("MAIN_DISCORD_INVITE", "https://discord.gg/fbJYSF2RfV")
MAIN_DISCORD_GUILD_ID = os.getenv("MAIN_DISCORD_GUILD_ID")
CREATE_MISSING_RANK_ROLES = os.getenv("CREATE_MISSING_RANK_ROLES", "true").lower() not in {"0", "false", "no"}
RANK_ROLE_NAMES = {
    rank_name: os.getenv(f"RANK_ROLE_{rank_name.upper()}_NAME", rank_name)
    for rank_name, _ in RANK_THRESHOLDS
}
RANK_ROLE_IDS = {
    rank_name: os.getenv(f"RANK_ROLE_{rank_name.upper()}_ID")
    for rank_name, _ in RANK_THRESHOLDS
}
RANK_ROLE_COLORS = {
    "Silver": discord.Color(0xC0C0C0),
    "Gold": discord.Color.gold(),
    "Diamond": discord.Color.purple(),
    "Champ": discord.Color.red(),
    "Challenger": discord.Color.blue(),
}
RANK_CASH_MULTIPLIERS = {
    "Silver": 1.0,
    "Gold": 1.1,
    "Diamond": 1.2,
    "Champ": 1.3,
    "Master": 1.3,
    "Challenger": 1.5,
}


class Users(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.users = self.load_users()
        self.moderators = self.load_moderators()
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

    def load_moderators(self):
        data = load_json(MODERATORS_PATH, default=[])
        if isinstance(data, list):
            return {str(user_id) for user_id in data if str(user_id).isdigit()}
        if isinstance(data, dict):
            return {str(user_id) for user_id in data.get("moderators", []) if str(user_id).isdigit()}
        return set()

    def save_moderators(self):
        save_json(MODERATORS_PATH, sorted(self.moderators, key=int))

    async def is_bot_owner(self, user):
        try:
            return await self.bot.is_owner(user)
        except discord.DiscordException:
            return False

    async def is_moderator_manager(self, ctx):
        if str(ctx.author.id) in MODERATOR_MANAGER_IDS:
            return True
        if await self.is_bot_owner(ctx.author):
            return True
        guild_permissions = getattr(ctx.author, "guild_permissions", None)
        return bool(getattr(guild_permissions, "administrator", False))

    async def is_moderator(self, ctx):
        if await self.is_moderator_manager(ctx):
            return True
        return str(ctx.author.id) in self.moderators

    async def moderator_check(self, ctx):
        if await self.is_moderator(ctx):
            return True
        raise commands.MissingPermissions(["bot_moderator"])

    async def moderator_manager_check(self, ctx):
        if await self.is_moderator_manager(ctx):
            return True
        raise commands.MissingPermissions(["administrator"])

    def build_data_backup(self):
        source_dir = data_path("data")
        source_dir.mkdir(parents=True, exist_ok=True)

        backup = tempfile.NamedTemporaryFile(
            prefix=f"propulse-data-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}-",
            suffix=".zip",
            delete=False,
        )
        backup_path = Path(backup.name)
        backup.close()

        with zipfile.ZipFile(backup_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(source_dir.rglob("*")):
                if not file_path.is_file():
                    continue
                if file_path.suffix == ".tmp" or file_path.name.startswith("."):
                    continue
                archive.write(file_path, Path("data") / file_path.relative_to(source_dir))

        return backup_path

    def get_profile(self, member: discord.Member):
        uid = str(member.id)
        if uid not in self.users:
            self.users[uid] = {
                'cash': STARTING_CASH,
                'gold': 0,
                'last_practice': None, 
                'last_daily': None,
                'last_ranked': None,
                'packs': [], 
                'cards': [],
                'team': {},
                'teams': self.default_teams(),
                'xp': 0,
                'level': 1,
                'team_stats': self.default_team_stats(),
                'team_stats_by_game': self.default_team_stats_by_game(),
                'team_stat_level': 1,
                'default_team_game': DEFAULT_TEAM_GAME,
                'elo': DEFAULT_ELO,
                'ranked_wins': 0,
                'ranked_losses': 0,
                'discord_username': member.name,
                'settings': self.default_settings()
                }
            self.save_users()
        else:
            self.users[uid]["discord_username"] = member.name
            self.normalize_settings(self.users[uid])
            self.users[uid].setdefault("team", {})
            self.normalize_game_teams(self.users[uid])
            self.users[uid].setdefault("last_ranked", None)
            self.normalize_profile_progress(self.users[uid])
            self.normalize_profile_currency(self.users[uid])
            self.save_users()
        return self.users[uid]

    def get_profile_by_id(self, user_id):
        uid = str(user_id)
        if uid not in self.users:
            self.users[uid] = {
                'cash': STARTING_CASH,
                'gold': 0,
                'last_practice': None,
                'last_daily': None,
                'last_ranked': None,
                'packs': [],
                'cards': [],
                'team': {},
                'teams': self.default_teams(),
                'xp': 0,
                'level': 1,
                'team_stats': self.default_team_stats(),
                'team_stats_by_game': self.default_team_stats_by_game(),
                'team_stat_level': 1,
                'default_team_game': DEFAULT_TEAM_GAME,
                'elo': DEFAULT_ELO,
                'ranked_wins': 0,
                'ranked_losses': 0,
                'discord_username': None,
                'settings': self.default_settings()
            }
            self.save_users()
        else:
            self.normalize_settings(self.users[uid])
            self.users[uid].setdefault("team", {})
            self.normalize_game_teams(self.users[uid])
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

    def default_teams(self):
        return {game_name: {} for game_name in TEAM_STAT_ROLES_BY_GAME}

    def default_team_stats_by_game(self):
        return {
            game_name: {role: BASE_TEAM_STAT for role in roles}
            for game_name, roles in TEAM_STAT_ROLES_BY_GAME.items()
        }

    def valorant_stats_from_league_stats(self, league_stats):
        return {
            valorant_role: int(league_stats.get(league_role, BASE_TEAM_STAT))
            for valorant_role, league_role in VALORANT_STAT_ROLE_MAP.items()
        }

    def normalize_game_name(self, game_name):
        text = str(game_name or "").strip().lower()
        if text in {"valorant", "val", "vct"}:
            return GAME_VALORANT
        return GAME_LEAGUE

    def normalize_game_teams(self, profile):
        teams = profile.get("teams")
        if not isinstance(teams, dict):
            teams = self.default_teams()
            profile["teams"] = teams

        legacy_team = profile.get("team")
        if not isinstance(legacy_team, dict):
            legacy_team = {}

        league_team = teams.get(GAME_LEAGUE)
        if not isinstance(league_team, dict):
            league_team = {}
            teams[GAME_LEAGUE] = league_team
        for role in TEAM_STAT_ROLES:
            if role in legacy_team and role not in league_team:
                league_team[role] = legacy_team[role]
        teams.setdefault(GAME_VALORANT, {})
        profile["team"] = league_team

        default_game = self.normalize_game_name(profile.get("default_team_game"))
        if default_game not in TEAM_STAT_ROLES_BY_GAME:
            default_game = DEFAULT_TEAM_GAME
        profile["default_team_game"] = default_game
        return teams

    def normalize_team_stats_by_game(self, profile):
        stats_by_game = profile.get("team_stats_by_game")
        if not isinstance(stats_by_game, dict):
            stats_by_game = {}
            profile["team_stats_by_game"] = stats_by_game

        legacy_stats = profile.get("team_stats")
        if not isinstance(legacy_stats, dict):
            legacy_stats = {}

        league_stats = stats_by_game.get(GAME_LEAGUE)
        if not isinstance(league_stats, dict):
            league_stats = {}
            stats_by_game[GAME_LEAGUE] = league_stats
        for role in TEAM_STAT_ROLES:
            league_stats.setdefault(role, legacy_stats.get(role, BASE_TEAM_STAT))

        stats_by_game[GAME_VALORANT] = self.valorant_stats_from_league_stats(league_stats)
        profile["team_stats"] = league_stats
        return stats_by_game

    def normalize_team_stats(self, profile):
        stats_by_game = self.normalize_team_stats_by_game(profile)

        profile.setdefault("team_stat_level", 1)
        while int(profile["team_stat_level"]) < int(profile.get("level", 1)):
            self.apply_team_stat_level_up(profile)

    def apply_team_stat_level_up(self, profile):
        stats_by_game = self.normalize_team_stats_by_game(profile)
        gains = {}
        league_stats = stats_by_game[GAME_LEAGUE]
        league_gains = {}
        for role in TEAM_STAT_ROLES:
            league_stats.setdefault(role, BASE_TEAM_STAT)
            gain = random.randint(STAT_GAIN_MIN, STAT_GAIN_MAX)
            league_stats[role] += gain
            league_gains[role] = gain
        stats_by_game[GAME_VALORANT] = self.valorant_stats_from_league_stats(league_stats)
        gains[GAME_LEAGUE] = league_gains
        profile["team_stat_level"] = int(profile.get("team_stat_level", 1)) + 1
        return gains

    def normalize_profile_progress(self, profile):
        profile.setdefault("xp", 0)
        profile["level"] = self.level_for_xp(int(profile.get("xp", 0)))
        self.normalize_game_teams(profile)
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

    async def get_main_discord_guild(self):
        guild_id = int(MAIN_DISCORD_GUILD_ID) if str(MAIN_DISCORD_GUILD_ID or "").isdigit() else None

        if guild_id is None and MAIN_DISCORD_INVITE:
            try:
                invite = await self.bot.fetch_invite(MAIN_DISCORD_INVITE)
                guild_id = invite.guild.id if invite and invite.guild else None
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                guild_id = None

        if guild_id is None:
            return None, "Set MAIN_DISCORD_GUILD_ID or MAIN_DISCORD_INVITE so I know which server to manage."

        guild = self.bot.get_guild(guild_id)
        if guild is None:
            try:
                guild = await self.bot.fetch_guild(guild_id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                guild = None

        if guild is None:
            return None, "I could not access the main Discord. Make sure the bot is in that server."
        return guild, None

    async def get_rank_role(self, guild, rank_name, create_missing=False):
        role_name = RANK_ROLE_NAMES.get(rank_name, rank_name)
        role_color = RANK_ROLE_COLORS.get(rank_name, discord.Color.default())
        role_id = RANK_ROLE_IDS.get(rank_name)
        if role_id and str(role_id).isdigit():
            role = guild.get_role(int(role_id))
            if role:
                if create_missing:
                    error = await self.update_rank_role_appearance(role, rank_name, role_name, role_color)
                    if error:
                        return None, error
                return role, None

        role = discord.utils.get(guild.roles, name=role_name)
        if role:
            if create_missing:
                error = await self.update_rank_role_appearance(role, rank_name, role_name, role_color)
                if error:
                    return None, error
            return role, None

        legacy_role = discord.utils.get(guild.roles, name=f"Ranked {rank_name}")
        if legacy_role and create_missing:
            error = await self.update_rank_role_appearance(legacy_role, rank_name, role_name, role_color)
            if error:
                return None, error
            return legacy_role, None

        if not create_missing:
            return None, None
        if not CREATE_MISSING_RANK_ROLES:
            return None, f"Missing role `{role_name}`."

        me = guild.me or guild.get_member(self.bot.user.id)
        if me is None:
            try:
                me = await guild.fetch_member(self.bot.user.id)
            except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                me = None
        if not me or not me.guild_permissions.manage_roles:
            return None, "I need Manage Roles permission to create missing ranked roles."

        try:
            role = await guild.create_role(
                name=role_name,
                color=role_color,
                reason="Create ProPulse ranked role",
            )
            return role, None
        except (discord.Forbidden, discord.HTTPException) as exc:
            return None, f"Could not create `{role_name}`: {exc}"

    async def update_rank_role_appearance(self, role, rank_name, role_name, role_color):
        if role.name == role_name and role.color == role_color:
            return None

        try:
            await role.edit(
                name=role_name,
                color=role_color,
                reason=f"Update ProPulse {rank_name} rank role appearance",
            )
            return None
        except discord.Forbidden:
            return "I need Manage Roles permission, and my bot role must be above the ranked roles."
        except discord.HTTPException as exc:
            return f"Discord rejected the role update: {exc}"

    async def sync_rank_role_for_user(self, user_id, elo, reason="Sync ProPulse ranked role"):
        guild, error = await self.get_main_discord_guild()
        if error:
            return error

        try:
            member = guild.get_member(int(user_id)) or await guild.fetch_member(int(user_id))
        except discord.NotFound:
            return None
        except discord.Forbidden:
            return "I could not check members in the main Discord."
        except discord.HTTPException as exc:
            return f"Discord rejected the member lookup: {exc}"

        rank_name = self.rank_for_elo(int(elo))
        wanted_role, error = await self.get_rank_role(guild, rank_name, create_missing=True)
        if error:
            return error
        if wanted_role is None:
            return f"Missing role `{RANK_ROLE_NAMES.get(rank_name, rank_name)}`."

        rank_roles = []
        for existing_rank in RANK_ROLE_NAMES:
            role, _ = await self.get_rank_role(guild, existing_rank, create_missing=False)
            if role:
                rank_roles.append(role)
            legacy_role = discord.utils.get(guild.roles, name=f"Ranked {existing_rank}")
            if legacy_role:
                rank_roles.append(legacy_role)

        current_role_ids = {role.id for role in member.roles}
        roles_to_remove = [
            role for role in rank_roles
            if role.id != wanted_role.id and role.id in current_role_ids
        ]

        try:
            if roles_to_remove:
                await member.remove_roles(*roles_to_remove, reason=reason)
            if wanted_role.id not in current_role_ids:
                await member.add_roles(wanted_role, reason=reason)
        except discord.Forbidden:
            return "I need Manage Roles permission, and my bot role must be above the ranked roles."
        except discord.HTTPException as exc:
            return f"Discord rejected the role update: {exc}"

        return None

    async def ensure_all_rank_roles(self):
        guild, error = await self.get_main_discord_guild()
        if error:
            return [error]

        errors = []
        for rank_name, _ in RANK_THRESHOLDS:
            _role, error = await self.get_rank_role(guild, rank_name, create_missing=True)
            if error:
                errors.append(f"{rank_name}: {error}")
        return errors

    async def sync_all_rank_roles(self):
        setup_errors = await self.ensure_all_rank_roles()
        errors = []
        synced = 0
        for user_id, user_data in self.users.items():
            if not isinstance(user_data, dict):
                continue
            self.normalize_profile_progress(user_data)
            error = await self.sync_rank_role_for_user(
                user_id,
                user_data.get("elo", DEFAULT_ELO),
                "Sync all ProPulse ranked roles",
            )
            if error:
                errors.append(f"{user_id}: {error}")
                continue
            synced += 1
        return synced, setup_errors + errors

    def daily_cash_reward(self, profile):
        rank = self.rank_for_elo(int(profile.get("elo", DEFAULT_ELO)))
        multiplier = RANK_CASH_MULTIPLIERS.get(rank, 1.0)
        base_reward = random.randint(20, 50)
        return base_reward, round(base_reward * multiplier), rank, multiplier

    def topgg_bot_id(self):
        if TOPGG_BOT_ID:
            return TOPGG_BOT_ID
        if self.bot and self.bot.user:
            return str(self.bot.user.id)
        return None

    def topgg_vote_url(self):
        bot_id = self.topgg_bot_id()
        if not bot_id:
            return "https://top.gg/"
        return f"https://top.gg/bot/{bot_id}/vote"

    def parse_topgg_time(self, value):
        if not value:
            return None
        try:
            saved = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
        if saved.tzinfo is None:
            return saved.replace(tzinfo=timezone.utc)
        return saved.astimezone(timezone.utc)

    def fetch_topgg_vote_status_sync(self, user_id):
        url = f"https://top.gg/api/v1/projects/@me/votes/{quote(str(user_id))}"
        response = requests.get(
            url,
            headers={"Authorization": f"Bearer {TOPGG_TOKEN}"},
            params={"source": "discord"},
            timeout=10,
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return response.json()

    async def fetch_topgg_vote_status(self, user_id):
        return await asyncio.to_thread(self.fetch_topgg_vote_status_sync, user_id)

    def team_slot_has_card(self, profile, role):
        teams = self.normalize_game_teams(profile)
        game_name = profile.get("default_team_game", DEFAULT_TEAM_GAME)
        team = teams.get(game_name) if isinstance(teams.get(game_name), dict) else {}
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
        cards_cog = self.bot.get_cog("Cards") if self.bot else None
        if cards_cog is not None:
            game_name = cards_cog.get_default_team_game(profile)
            defense_slots = cards_cog.get_best_ranked_defense_slots(profile, game_name)
            if defense_slots is None:
                image_slots, _ = cards_cog.get_team_image_slots(
                    profile,
                    game_name,
                    1.0,
                )
                cards_cog.remember_best_ranked_defense(profile, game_name, image_slots)
                defense_slots = image_slots
            return cards_cog.ranked_team_power(
                profile,
                defense_slots,
                game_name,
                1.0,
            )

        game_name = self.normalize_game_name(profile.get("default_team_game"))
        stats = self.normalize_team_stats_by_game(profile).get(game_name, {})
        if not isinstance(stats, dict):
            stats = {}
        return sum(
            self.team_slot_power(stats, role, self.team_slot_has_card(profile, role))
            for role in TEAM_STAT_ROLES_BY_GAME.get(game_name, TEAM_STAT_ROLES)
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
            "confirm_pack_buy": True,
            "show_name_in_challenger_pulls": True,
            "language": "en",
        }

    def normalize_settings(self, profile):
        settings = profile.get("settings")
        if not isinstance(settings, dict):
            settings = {}
            profile["settings"] = settings
        defaults = self.default_settings()
        for key, value in defaults.items():
            settings.setdefault(key, value)
        settings["language"] = translator.normalize_language(settings.get("language"))
        return settings

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
            settings = self.normalize_settings(user)
            if not settings.get("alert_daily_practice"):
                continue

            self.schedule_user_ready_notifications(user_id, user, now)


    # Commands 

    # Help command to list available commands and filters
    def add_moderator_help(self, embed):
        embed.add_field(
            name="Moderator",
            value=(
                "`.help -mod` - Show moderator commands.\n"
                "`.backupdata` - Send a zip of the current data files.\n"
                "`.syncrankroles` or `.syncranks` - Sync ranked Discord roles.\n"
                "`.mods` - List bot moderators.\n"
                "`.addmod @user` - Add a bot moderator. Admins, bot owners, and manager IDs only.\n"
                "`.removemod @user` - Remove a bot moderator. Admins, bot owners, and manager IDs only."
            ),
            inline=False
        )

    @commands.command()
    async def help(self, ctx, section: str = None):
        if str(section or "").lower() in {"-mod", "mod", "mods", "moderator", "moderators"}:
            await self.moderator_check(ctx)
            embed = discord.Embed(title="Moderator Commands", color=discord.Color.dark_grey())
            self.add_moderator_help(embed)
            await ctx.send(embed=embed)
            return

        embed = discord.Embed(title="Available Commands", color=discord.Color.dark_grey())
        embed.add_field(
            name="User",
            value=(
                "`.help` - Show this command list.\n"
                "`.profile [@user]` or `.prof [@user]` - View a profile.\n"
                "`.cd` - Check practice, daily, ranked, and vote cooldowns.\n"
                "`.daily` - Earn cash and gold every 24 hours.\n"
                "`.vote` - Vote on Top.gg and claim 50 cash plus 5 gold."
            ),
            inline=False
        )
        embed.add_field(
            name="Ranked",
            value=(
                "`.leaderboard` or `.lb` - View users ranked by ELO.\n"
                "`.ranked` or `.r` - Play a ranked match to earn cash and XP.\n"
                "`.practice` or `.p` - Earn cash and XP every 10 minutes.\n"
                "`.team` - View your LoL or Valorant lineup."
            ),
            inline=False
        )
        embed.add_field(
            name="Collection",
            value=(
                "`.inventory [filters]` or `.inv [filters]` - View your cards.\n"
                "Filters: `-player`, `-team`, `-game`, `-rarity`, `-set`, `-league`, `-role`.\n"
                "Example: `.inv -game LoL -team T1 -rarity Gold -role mid`\n"
                "`.progress [filters]` - View collection progress.\n"
                "`.completion` - View set completion and power bonuses.\n"
                "`.info [filters]` or `.info <CID>` - List or inspect bot cards.\n"
                "`.view <inventory #>` - View one inventory card.\n"
                "`.packs` - View unopened packs.\n"
                "`.open <pack #|pack id|pack name>` - Open a pack.\n"
                "`.open -all` - Open your packs one at a time from lowest index."
            ),
            inline=False
        )
        embed.add_field(
            name="Predictions",
            value="`.prediction`, `.predictions`, `.pred`, or `.preds` - View matches and manage your picks.",
            inline=False
        )
        embed.add_field(
            name="Economy",
            value=(
                "`.shop` - View packs for sale and buy packs.\n"
                "`.auction` - View auctions with filters, bidding, and buy now.\n"
                "Auction filters include `-progress` / `-needed`.\n"
                "`.auction -sell <inventory #>` - Auction a card.\n"
                "`.auction -sellpack <pack #>` - Auction a pack.\n"
                "`.autosell` - Auction duplicate cards using autosell settings.\n"
                "`.autosell -settings` - Configure autosell.\n"
                "`.trade @user` - Start a trade."
            ),
            inline=False
        )
        await ctx.send(embed=embed)

    # Profile command to show user's balance and cards owned
    @commands.command(aliases=["prof"])
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
        settings = self.normalize_settings(profile)
        embed.add_field(
            name="Alerts/Confirmations",
            value=(
                f"{ALERT_EMOJI} Cooldown Alerts: {'ON' if settings['alert_daily_practice'] else 'OFF'}\n"
                f"{DM_EMOJI} Auction DMs: {'ON' if settings['dm_auction_notis'] else 'OFF'}\n"
                f"{CONFIRM_EMOJI} Auction Confirm Buy: {'ON' if settings['confirm_auction_buy'] else 'OFF'}\n"
                f"{PACK_EMOJI} Shop Confirm Buy: {'ON' if settings['confirm_pack_buy'] else 'OFF'}\n"
                f"{CARDS_EMOJI} Show name in Challenger-Pulls: {'ON' if settings['show_name_in_challenger_pulls'] else 'OFF'}\n"
                f"Language: {available_languages().get(settings['language'], 'English')}"
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

    @commands.command(aliases=["addmoderator", "modadd"])
    @commands.check(lambda ctx: ctx.cog.moderator_manager_check(ctx))
    async def addmod(self, ctx, member: discord.Member):
        self.moderators.add(str(member.id))
        self.save_moderators()
        await ctx.send(f"{member.mention} can now use bot moderator commands.")

    @commands.command(aliases=["removemoderator", "modremove", "delmod"])
    @commands.check(lambda ctx: ctx.cog.moderator_manager_check(ctx))
    async def removemod(self, ctx, member: discord.Member):
        if str(member.id) not in self.moderators:
            await ctx.send(f"{member.mention} is not a bot moderator.")
            return

        self.moderators.remove(str(member.id))
        self.save_moderators()
        await ctx.send(f"{member.mention} can no longer use bot moderator commands.")

    @commands.command(aliases=["moderators"])
    @commands.check(lambda ctx: ctx.cog.moderator_check(ctx))
    async def mods(self, ctx):
        if not self.moderators:
            await ctx.send("No bot moderators have been added yet. Server admins and bot owners can still manage moderator commands.")
            return

        mentions = []
        for user_id in sorted(self.moderators, key=int):
            member = ctx.guild.get_member(int(user_id)) if ctx.guild else None
            mentions.append(member.mention if member else f"`{user_id}`")
        await ctx.send("Bot moderators: " + ", ".join(mentions))

    @commands.command(aliases=["syncranks"])
    @commands.check(lambda ctx: ctx.cog.moderator_check(ctx))
    async def syncrankroles(self, ctx):
        await ctx.send("Syncing ranked roles in the main Discord...")
        synced, errors = await self.sync_all_rank_roles()
        message = f"Synced ranked roles for {synced} member(s)."
        if errors:
            message += f" {len(errors)} member(s) could not be synced. First issue: {errors[0]}"
        await ctx.send(message)

    @commands.command(aliases=["backup", "databackup", "zipdata"])
    @commands.check(lambda ctx: ctx.cog.moderator_check(ctx))
    async def backupdata(self, ctx):
        await ctx.send("Creating data backup...")
        backup_path = self.build_data_backup()
        filename = f"propulse-data-{datetime.now(timezone.utc):%Y%m%d-%H%M%S}.zip"

        try:
            await ctx.send(
                "Here is the current ProPulse data backup.",
                file=discord.File(backup_path, filename=filename),
            )
        except discord.HTTPException as exc:
            await ctx.send(f"Discord rejected the backup upload: {exc}")
        finally:
            try:
                backup_path.unlink()
            except OSError:
                pass

    @commands.command(aliases=["lang"])
    async def language(self, ctx, language_code: str = None):
        user = self.get_profile(ctx.author)
        settings = self.normalize_settings(user)

        if language_code is None:
            available = ", ".join(
                f"`{code}` ({name})"
                for code, name in available_languages().items()
            )
            await ctx.send(
                f"Your language is `{settings['language']}`. Available languages: {available}."
            )
            return

        language_code = translator.normalize_language(language_code)
        settings["language"] = language_code
        self.save_users()
        await ctx.send(f"Language set to {available_languages()[language_code]}.")

    # CD (cooldown) command to show how long until user can practice or claim daily again
    @commands.command()
    async def cd(self, ctx):
        user = self.get_profile(ctx.author)
        now = self.utc_now()

        practice_cd = f"{READY_EMOJI} Ready"
        daily_cd = f"{READY_EMOJI} Ready"
        ranked_cd = f"{READY_EMOJI} Ready"
        vote_cd = f"{READY_EMOJI} Ready"

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

        if TOPGG_TOKEN:
            try:
                vote_data = await self.fetch_topgg_vote_status(ctx.author.id)
            except requests.exceptions.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 429:
                    vote_cd = "Rate limited"
                elif status in {401, 403}:
                    vote_cd = "Token rejected"
                else:
                    vote_cd = "Check failed"
            except requests.exceptions.RequestException:
                vote_cd = "Check failed"
            else:
                if vote_data:
                    expires_at = self.parse_topgg_time(vote_data.get("expires_at"))
                    reward_key = str(vote_data.get("created_at") or vote_data.get("expires_at") or "")
                    if reward_key and user.get("last_topgg_vote_rewarded_at") != reward_key:
                        vote_cd = "Claim"
                    elif expires_at and now < expires_at:
                        vote_cd = self.format_remaining(expires_at)
                    else:
                        vote_cd = f"{READY_EMOJI} Ready"
        else:
            vote_cd = "Vote link ready"

        embed = discord.Embed(title=f"{COOLDOWN_EMOJI} {ctx.author.display_name}'s Cooldowns")
        embed.add_field(
            name="\u200b",
            value=(
                f"{PRACTICE_EMOJI} **Practice**\n"
                f"{practice_cd}\n\n"
                f"{VOTE_EMOJI} **Vote**\n"
                f"{vote_cd}"
            ),
            inline=True,
        )
        embed.add_field(
            name="\u200b",
            value=(
                f"{RANKED_EMOJI} **Ranked**\n"
                f"{ranked_cd}\n\n"
                f"{DAILY_EMOJI} **Daily**\n"
                f"{daily_cd}"
            ),
            inline=True,
        )

        await ctx.send(embed=embed)


    # Practice command to earn cash every 10 minutes
    @commands.command(aliases=["p"])
    async def practice(self, ctx):
        user = self.get_profile(ctx.author)
        now = self.utc_now()

        last_time = self.parse_saved_time(user.get("last_practice"))

        if last_time:
            practice_ready_at = last_time + PRACTICE_COOLDOWN
            if now < practice_ready_at:
                await ctx.send(f"{WAIT_EMOJI} Wait {self.format_remaining(practice_ready_at)} before practicing again.")
                return

        reward = random.randint(10, 20)
        xp_reward = random.randint(PRACTICE_XP_MIN, PRACTICE_XP_MAX)
        leveled_up, stat_gains = self.add_xp(user, xp_reward)
        user["cash"] += reward
        user["last_practice"] = now.isoformat()
        self.remember_reminder_channel(user, "practice", ctx.channel.id)

        self.save_users()

        level_text = ""
        if leveled_up:
            latest_gains = stat_gains[-1] if stat_gains else {}
            gain_parts = []
            for key, value in latest_gains.items():
                if isinstance(value, dict):
                    inner = ", ".join(f"{role} +{gain}" for role, gain in value.items())
                    if inner:
                        gain_parts.append(f"{key}: {inner}")
                else:
                    gain_parts.append(f"{key} +{value}")
            gain_text = "; ".join(gain_parts)
            level_text = f" You leveled up to **Level {user['level']}**."
            if gain_text:
                level_text += f" Stat gains: {gain_text}."
        current_xp, needed_xp = self.xp_progress_for_level(user["xp"], user["level"])
        await ctx.send(
            f"{PRACTICE_EMOJI} {ctx.author.mention} You practiced and earned "
            f"{reward} {CASH_EMOJI} and {xp_reward} XP.{level_text}\n"
            f"Total: {user['cash']} {CASH_EMOJI} \n"
            f"{current_xp}/{needed_xp} XP towards Level {user['level'] + 1}."
        )
        if self.normalize_settings(user).get("alert_daily_practice"):
            ready_at = now + PRACTICE_COOLDOWN
            self.schedule_ready_notification(ctx.channel.id, ctx.author.id, "practice", ready_at)

    @commands.command()
    async def vote(self, ctx):
        user = self.get_profile(ctx.author)
        vote_url = self.topgg_vote_url()
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Vote on Top.gg", url=vote_url))

        if not TOPGG_TOKEN:
            await ctx.send(
                f"Please vote on top.gg with this link:\n{vote_url}\n\n"
                "After voting use `.vote` to claim your reward.\n"
                "I need `TOPGG_TOKEN` configured before I can verify votes and give rewards.",
                view=view,
            )
            return

        try:
            vote_data = await self.fetch_topgg_vote_status(ctx.author.id)
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in {401, 403}:
                await ctx.send(
                    f"Please vote on top.gg with this link:\n{vote_url}\n\n"
                    "After voting use `.vote` to claim your reward.\n"
                    "I could not verify votes because the Top.gg token was rejected.",
                    view=view,
                )
                return
            if status == 429:
                await ctx.send("Top.gg is rate limiting vote checks right now. Try again in a minute.", view=view)
                return
            await ctx.send(
                f"Please vote on top.gg with this link:\n{vote_url}\n\n"
                "After voting use `.vote` to claim your reward.\n"
                "I could not verify Top.gg right now.",
                view=view,
            )
            return
        except requests.exceptions.RequestException:
            await ctx.send(
                f"Please vote on top.gg with this link:\n{vote_url}\n\n"
                "After voting use `.vote` to claim your reward.\n"
                "I could not reach Top.gg right now.",
                view=view,
            )
            return

        if not vote_data:
            await ctx.send(
                f"Please vote on top.gg with this link:\n{vote_url}\n\n"
                "After voting use `.vote` to claim your reward.",
                view=view,
            )
            return

        created_at_raw = vote_data.get("created_at")
        expires_at = self.parse_topgg_time(vote_data.get("expires_at"))
        if expires_at and self.utc_now() >= expires_at:
            await ctx.send(
                f"Please vote on top.gg with this link:\n{vote_url}\n\n"
                "After voting use `.vote` to claim your reward.",
                view=view,
            )
            return

        reward_key = str(created_at_raw or vote_data.get("expires_at") or "")
        if reward_key and user.get("last_topgg_vote_rewarded_at") == reward_key:
            wait_text = f" You can vote again in {self.format_remaining(expires_at)}." if expires_at else ""
            await ctx.send(
                f"You already claimed the reward for your latest Top.gg vote.{wait_text}",
                view=view,
            )
            return

        user["cash"] = int(user.get("cash", 0)) + VOTE_CASH_REWARD
        user["gold"] = int(user.get("gold", 0)) + VOTE_GOLD_REWARD
        if reward_key:
            user["last_topgg_vote_rewarded_at"] = reward_key
        self.save_users()

        await ctx.send(
            f"Thanks for voting, {ctx.author.mention}! You earned "
            f"{VOTE_CASH_REWARD} {CASH_EMOJI} cash and {VOTE_GOLD_REWARD} {GOLD_EMOJI} gold.\n"
            f"Total: {user['cash']} {CASH_EMOJI} cash and {user['gold']} {GOLD_EMOJI} gold."
        )

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
        if self.normalize_settings(user).get("alert_daily_practice"):
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
                f"Power: `{entry['total_power']}` | {RANKED_EMOJI} ELO: `{entry['elo']}`"
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


class LanguageSelect(discord.ui.Select):
    def __init__(self, settings_view):
        self.settings_view = settings_view
        settings = settings_view.get_settings()
        current_language = settings.get("language", "en")
        options = [
            discord.SelectOption(
                label=name,
                value=code,
                description=f"Use {name} for bot messages.",
                default=code == current_language,
            )
            for code, name in available_languages().items()
        ]
        super().__init__(
            placeholder="Choose a language",
            min_values=1,
            max_values=1,
            options=options[:25],
        )

    async def callback(self, interaction: discord.Interaction):
        language_code = translator.normalize_language(self.values[0])
        profile = self.settings_view.users_cog.get_profile_by_id(self.settings_view.target_uid)
        settings = self.settings_view.users_cog.normalize_settings(profile)
        settings["language"] = language_code
        self.settings_view.users_cog.save_users()
        self.settings_view.refresh_button_styles()

        language_name = available_languages()[language_code]
        await interaction.response.edit_message(
            content=f"Language set to {language_name}.",
            view=None,
        )
        if self.settings_view.message is not None:
            await self.settings_view.message.edit(view=self.settings_view)


class LanguageSelectView(discord.ui.View):
    def __init__(self, settings_view):
        super().__init__(timeout=60)
        self.settings_view = settings_view
        self.add_item(LanguageSelect(settings_view))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.settings_view.requester_id:
            await interaction.response.send_message("You can only edit your own settings.", ephemeral=True)
            return False
        return True


class ProfileSettingsView(discord.ui.View):
    BUTTON_SETTINGS = {
        "profile_settings:alert_daily_practice": "alert_daily_practice",
        "profile_settings:dm_auction_notis": "dm_auction_notis",
        "profile_settings:confirm_auction_buy": "confirm_auction_buy",
        "profile_settings:confirm_pack_buy": "confirm_pack_buy",
        "profile_settings:show_name_in_challenger_pulls": "show_name_in_challenger_pulls",
    }

    def __init__(self, users_cog: Users, requester_id: int, target_uid: str):
        super().__init__(timeout=180)
        self.users_cog = users_cog
        self.requester_id = requester_id
        self.target_uid = target_uid
        self.message = None
        self.refresh_button_styles()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message("You can only edit your own settings.", ephemeral=True)
            return False
        return True

    def toggle(self, key):
        profile = self.users_cog.get_profile_by_id(self.target_uid)
        settings = self.users_cog.normalize_settings(profile)
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
        return self.users_cog.normalize_settings(profile)

    def refresh_button_styles(self):
        settings = self.get_settings()
        for item in self.children:
            key = self.BUTTON_SETTINGS.get(getattr(item, "custom_id", None))
            if key:
                item.style = discord.ButtonStyle.success if settings.get(key, True) else discord.ButtonStyle.danger

    async def send_toggle_response(self, interaction: discord.Interaction, message: str):
        await interaction.response.send_message(message, ephemeral=True)
        self.message = interaction.message
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

    @discord.ui.button(label=f"{CARDS_EMOJI} Show name in Challenger-Pulls", style=discord.ButtonStyle.secondary, custom_id="profile_settings:show_name_in_challenger_pulls")
    async def toggle_challenger_pull_name(self, interaction: discord.Interaction, button: discord.ui.Button):
        state = self.toggle("show_name_in_challenger_pulls")
        await self.send_toggle_response(interaction, f"Challenger-Pulls name display is now {'ON' if state else 'OFF'}.")

    @discord.ui.button(label="Language", style=discord.ButtonStyle.secondary, custom_id="profile_settings:language")
    async def choose_language(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.message = interaction.message
        await interaction.response.send_message(
            "Choose your bot language.",
            view=LanguageSelectView(self),
            ephemeral=True,
        )

async def setup(bot):
    await bot.add_cog(Users(bot))
