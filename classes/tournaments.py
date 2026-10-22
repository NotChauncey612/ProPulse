"""Weekly signups, durable BO5 broadcasts, and scheduled tournament brackets."""

import asyncio
import copy
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import discord
from discord.ext import commands, tasks

from . import tournament_engine as engine
from .tournament_engine import FINALS_GAME_INTERVAL, GAME_INTERVAL
from .cards import GAME_LEAGUE, GAME_VALORANT, RARITY_TEAM_MULTIPLIERS
from .tournament_bracket import render_bracket
from .storage import load_json, save_json


DATA_PATH = "data/tournaments.json"
DEFAULT_TIMEZONE = "America/New_York"
DEFAULT_MATCH_TIME = "17:00"
STAGE_LABELS = {"groups": "Group stage", "preliminary": "Preliminary round",
                "quarterfinal": "Quarterfinals", "semifinal": "Semifinals", "final": "Final"}
log = logging.getLogger(__name__)


def stamp(value, style="F"):
    return f"<t:{int(datetime.fromisoformat(value).timestamp())}:{style}>"


def mention(uid):
    return f"<@{uid}>" if uid else "BYE"


def short_name(entrant):
    return discord.utils.escape_markdown(entrant["name"][:40])


def game_interval(match):
    return FINALS_GAME_INTERVAL if match["stage"] in {"semifinal", "final"} else GAME_INTERVAL


def seeded_name(event, uid, escape=True):
    entrant = event["entrants"][uid]
    name = short_name(entrant) if escape else entrant["name"]
    seed = event.get("tournament_seeds", {}).get(uid)
    return f"#{seed} {name}" if seed else name


def seeded_mention(event, uid):
    seed = event.get("tournament_seeds", {}).get(uid)
    return f"#{seed} {mention(uid)}" if seed else mention(uid)


class SignupView(discord.ui.View):
    def __init__(self, cog):
        super().__init__(timeout=None)
        self.cog = cog

    async def act(self, interaction, leave=False):
        if not interaction.guild or interaction.user.bot:
            return
        await interaction.response.defer(ephemeral=True)
        async with self.cog.lock(interaction.guild.id):
            state = self.cog.guild_state(interaction.guild.id)
            event = state.get("event")
            if not event or event.get("board_id") != interaction.message.id:
                text = "This signup panel is from an older tournament. Use `.tournament` for this week."
            else:
                text = self.cog.register(state, interaction.user, leave)
                self.cog.save()
                await self.cog.refresh_board(event)
            players = self.cog.signup_embed(event) if event and event["state"] == "registration" else None
        await interaction.followup.send(text, embed=players, ephemeral=True)

    @discord.ui.button(label="Sign up", style=discord.ButtonStyle.green,
                       custom_id="propulse:tournament:join")
    async def join(self, interaction, button):
        await self.act(interaction)

    @discord.ui.button(label="Withdraw", style=discord.ButtonStyle.secondary,
                       custom_id="propulse:tournament:leave")
    async def leave(self, interaction, button):
        await self.act(interaction, leave=True)


class Tournaments(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_json(DATA_PATH, default={})
        self.data.setdefault("guilds", {})
        self.locks = {}
        self.signup_view = SignupView(self)

    async def cog_load(self):
        self.bot.add_view(self.signup_view)
        self.scheduler.start()

    def cog_unload(self):
        self.scheduler.cancel()
        self.signup_view.stop()

    async def cog_check(self, ctx):
        # Group checks are not inherited when invoke_without_command=True dispatches a subcommand.
        if ctx.guild is None:
            raise commands.NoPrivateMessage()
        return True

    def lock(self, guild_id):
        return self.locks.setdefault(str(guild_id), asyncio.Lock())

    def guild_state(self, guild_id):
        return self.data["guilds"].setdefault(str(guild_id), {})

    def save(self):
        save_json(DATA_PATH, self.data)

    async def moderator(self, ctx):
        users = self.bot.get_cog("Users")
        if not users:
            raise commands.CheckFailure("Users system is unavailable.")
        await users.moderator_check(ctx)

    def snapshot(self, user_id, game, name):
        cards = self.bot.get_cog("Cards")
        users = self.bot.get_cog("Users")
        profile = users.users.get(str(user_id)) if users else None
        if not cards or not profile:
            raise ValueError("Create your profile and set a full team with `.team` first.")
        # Work on a copy: inspecting an entrant must never normalize or level up their saved profile.
        profile = copy.deepcopy(profile)
        slots = cards.get_ranked_team_slots(profile, game)
        if len(slots) != 5 or any(not cards.slot_has_team_player(slot) for slot in slots):
            raise ValueError(f"You need five valid {game} cards in the correct roles. Set them with `.team`.")
        lineup = []
        for slot in slots:
            owned = slot["owned_card"]
            rarity = owned.get("rarity")
            if rarity not in RARITY_TEAM_MULTIPLIERS:
                raise ValueError("One of your team cards has an invalid rarity.")
            card = copy.deepcopy(slot["card_data"])
            lineup.append({
                "role": slot["role"], "game": game, "card_data": card,
                "owned_card": {"rarity": rarity}, "rarity": rarity,
                "player_name": slot["player"].get("name", card.get("ign", "Unknown")),
                "set_label": cards.format_team_set_label(card),
                "stat": engine.BASE_STAT,
                "power": round(engine.BASE_STAT * RARITY_TEAM_MULTIPLIERS[rarity]),
                "completion_multiplier": 1.0,
            })
        return {"name": name, "slots": lineup, "power": sum(slot["power"] for slot in lineup)}

    def register(self, state, member, leave=False, now=None):
        now = now or engine.utc_now()
        event = state.get("event")
        if not state.get("config", {}).get("enabled"):
            return "Tournament scheduling is paused."
        if (not event or event["state"] != "registration" or
                not engine.due(event["opens_at"], now) or engine.due(event["closes_at"], now)):
            return "Signups are open Monday through Wednesday. Use `.tournament` for the schedule."
        uid = str(member.id)
        if leave:
            if event["entrants"].pop(uid, None) is None:
                return "You are not signed up this week."
            event["board_dirty"] = True
            return "You withdrew from this week's tournament."
        if uid in event["entrants"]:
            return "You are already signed up for this week's tournament. Here are the players signed up so far."
        if member.bot:
            return "Bot accounts cannot enter."
        if uid not in event["entrants"] and len(event["entrants"]) >= engine.MAX_ENTRANTS:
            return f"This week's tournament is full ({engine.MAX_ENTRANTS} teams)."
        try:
            event["entrants"][uid] = self.snapshot(member.id, event["game"], member.display_name)
            event["board_dirty"] = True
        except ValueError as exc:
            return str(exc)
        return ("You're signed up for this week! Your current lineup will be locked when signups close. "
                "Keep five valid cards equipped until then. Sign up again next Monday for the next tournament.")

    def leaderboard_seed_order(self, event, record=True):
        # Use the leaderboard's exact ordering on copies: its power calculations can normalize profiles.
        users = copy.copy(self.bot.get_cog("Users"))
        users.users = copy.deepcopy(users.users)
        entries = users.get_leaderboard_entries()
        positions = {str(entry["user_id"]): index for index, entry in enumerate(entries, 1)}
        if any(uid not in positions for uid in event["entrants"]):
            raise ValueError("An entrant is missing from the leaderboard; cannot assign fair byes")
        if record:
            event["leaderboard_positions"] = {uid: positions[uid] for uid in event["entrants"]}
        return sorted(event["entrants"], key=positions.__getitem__)

    def signup_embed(self, event):
        ordered = self.leaderboard_seed_order(event, record=False) if event["entrants"] else []
        embed = discord.Embed(title=f"Tournament signups · {len(ordered)}/{engine.MAX_ENTRANTS} players",
                              description="\n".join(f"**#{index}** {short_name(event['entrants'][uid])}"
                                                    for index, uid in enumerate(ordered, 1)) or "No players have signed up yet.",
                              color=discord.Color.gold())
        embed.add_field(name="Signups close", value=stamp(event["closes_at"]), inline=False)
        embed.set_footer(text="Provisional seeds follow the leaderboard and lock when signups close. Use .tournament leave to withdraw.")
        return embed

    def close_registration(self, event, now):
        if event["state"] != "registration" or not engine.due(event["closes_at"], now):
            return False
        if not self.bot.get_cog("Cards") or not self.bot.get_cog("Users"):
            raise RuntimeError("Cannot lock tournament lineups until Cards and Users are loaded")
        excluded = []
        for uid, entrant in list(event["entrants"].items()):
            try:
                event["entrants"][uid] = self.snapshot(uid, event["game"], entrant["name"])
            except ValueError:
                excluded.append(uid)
                del event["entrants"][uid]
        event["excluded"] = excluded
        engine.lock_registration(event, leaderboard_order=self.leaderboard_seed_order(event))
        self.save()
        return True

    def seeds_for_guild(self, guild_id):
        state = self.data["guilds"].get(str(guild_id), {})
        event = state.get("event")
        if not event or event["state"] in {"registration", "cancelled"}:
            return {}
        if event["week"] != engine.week_start(engine.utc_now(), event["timezone"]).isoformat():
            return {}
        return dict(event.get("tournament_seeds", {}))

    def roll_game_gold(self, event, match):
        cards = self.bot.get_cog("Cards")
        if cards is None:
            raise RuntimeError("Cards must be loaded before a tournament game can run")
        result = {}
        for side in ("left", "right"):
            slots = event["entrants"][match[side]]["slots"]
            stats = {slot["role"]: slot.get("power", round(engine.BASE_STAT * RARITY_TEAM_MULTIPLIERS[slot["rarity"]]))
                     for slot in slots}
            rolls, total = cards.roll_ranked_gold_from_stats(stats, game_name=event["game"])
            result[f"{side}_gold_rolls"] = rolls
            result[f"{side}_gold"] = total
        return result

    async def channel(self, event):
        channel = self.bot.get_channel(event["channel_id"])
        if channel is None:
            channel = await self.bot.fetch_channel(event["channel_id"])
        return channel

    def board_embed(self, event):
        state = event["state"]
        embed = discord.Embed(title=f"Weekly {event['game']} Tournament · {event['week']}",
                              color=discord.Color.gold())
        embed.description = (
            "**Best of five · First to three wins**\n"
            "Equal base stats • Normal rarity multipliers • No level or collection bonuses\n"
            f"**{len(event['entrants'])}/{engine.MAX_ENTRANTS} teams** · {state.title()}\n"
            "Byes go to the highest-ranked entrants on the leaderboard at signup close.\n"
            f"Signups close {stamp(event['closes_at'])}\n"
            f"Thursday: groups (12+ teams only) {stamp(event['stage_times']['groups'])}\n"
            f"Friday: preliminaries / quarterfinals {stamp(event['stage_times']['quarterfinal'])}\n"
            f"Saturday: semifinals {stamp(event['stage_times']['semifinal'])}\n"
            f"Sunday: final {stamp(event['stage_times']['final'])}"
        )
        embed.add_field(name="🏆 Tournament prizes", value=(
            "1st: **20 gold + 500 cash**\n2nd: **10 gold + 250 cash**\n"
            "3rd–4th: **5 gold + 150 cash each** (semifinal losers)"), inline=False)
        if state == "registration":
            embed.add_field(name="Enter this week", value=(
                "Press **Sign up** or use `.tournament`. Your equipped team is locked at signup close.\n"
                "12–32 teams: Thursday groups; top two each advance. 5–11: start Friday.\n"
                "3–4: start Saturday. 2: Sunday final only. Every series is BO5; a new post reveals each game."), inline=False)
            ordered = self.leaderboard_seed_order(event, record=False) if event["entrants"] else []
            for offset in range(0, max(1, len(ordered)), 16):
                embed.add_field(name="Entrants · provisional seeds" if offset == 0 else "Entrants continued", value="\n".join(
                    f"#{index} {mention(uid)}" for index, uid in enumerate(ordered[offset:offset + 16], offset + 1))
                    or "Be the first to sign up!", inline=False)
        for group in event["groups"]:
            rows = engine.standings(event, group)
            embed.add_field(name=f"Group {group} · Top 2 advance", value="\n".join(
                f"{index}. {mention(row['uid'])} [seed {event['seeds'].index(row['uid']) + 1}] **{row['wins']}–{row['losses']}** "
                f"(games {row['game_wins']}–{row['game_losses']})"
                for index, row in enumerate(rows, 1)), inline=False)
        for stage in ("preliminary", "quarterfinal", "semifinal", "final"):
            matches = [match for match in event["matches"] if match["stage"] == stage]
            if not matches:
                continue
            lines = []
            for match in matches:
                a, b = engine.series_score(match)
                suffix = " · bye" if match.get("bye") else " · final" if match["winner"] else ""
                lines.append(f"{seeded_mention(event, match['left'])} **{a}–{b}** {seeded_mention(event, match['right'])}{suffix}")
            embed.add_field(name=STAGE_LABELS[stage], value="\n".join(lines), inline=False)
        if event.get("winner"):
            embed.add_field(name="🏆 Champion", value=mention(event["winner"]), inline=False)
        if event.get("reason"):
            embed.add_field(name="Tournament notice", value=event["reason"], inline=False)
        if event.get("excluded"):
            embed.add_field(name="Could not lock a valid lineup", value=" · ".join(mention(uid) for uid in event["excluded"]), inline=False)
        embed.set_footer(text="Tournament seeds lock from leaderboard order at signup close. Group ties: wins → game difference → game wins → seed. Times display in your local timezone.")
        return embed

    async def refresh_board(self, event):
        channel = await self.channel(event)
        view = self.signup_view if event["state"] == "registration" else None
        embed = self.board_embed(event)
        if event.get("board_id"):
            try:
                message = await channel.fetch_message(event["board_id"])
                await message.edit(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
                event["board_dirty"] = False
                self.save()
                return
            except discord.NotFound:
                pass
        message = await channel.send(embed=embed, view=view, allowed_mentions=discord.AllowedMentions.none())
        event["board_id"] = message.id
        event["board_dirty"] = False
        self.save()

    def match_embed(self, event, match, game_index=None):
        left, right = (event["entrants"][match[key]] for key in ("left", "right"))
        group = f" · Group {match['group']}" if match["group"] else ""
        names = {uid: seeded_name(event, uid) for uid in (match["left"], match["right"])}
        title = f"{names[match['left']]} vs {names[match['right']]} · {STAGE_LABELS[match['stage']]}{group}"
        embed = discord.Embed(title=title, color=discord.Color.blurple())
        if game_index is None:
            score = engine.series_score(match)
            embed.description = (f"{names[match['left']]} vs {names[match['right']]}\n"
                                 f"**{score[0]} – {score[1]}** · Best of five")
            if match["winner"]:
                embed.description += f"\n🏆 **Series winner: {names[match['winner']]}**"
            elif event["state"] == "cancelled":
                embed.description += "\nThis tournament was cancelled."
        else:
            games = match["games"][:game_index + 1]
            a = sum(game["winner"] == match["left"] for game in games)
            b = len(games) - a
            winner = games[-1]["winner"]
            embed.title += f" · Game {game_index + 1}"
            embed.description = (f"🎮 **Game winner: {names[winner]}**\n\n"
                                 f"{names[match['left']]} **{a} – {b}** {names[match['right']]}\n")
            if max(a, b) == 3:
                embed.description += f"🏆 **{names[winner]} wins the series {max(a, b)}–{min(a, b)}!**"
            elif a == b == 2:
                embed.description += "🔥 **2–2. Game five decides it!**"
            elif max(a, b) == 2:
                embed.description += "⚡ **Match point — one more win takes the series.**"
            embed.add_field(name="Games so far", value="\n".join(
                f"Game {index}: {names[game['winner']]}" for index, game in enumerate(games, 1)), inline=False)
        chance = engine.win_chance(left["power"], right["power"])
        embed.set_footer(text=f"Per-game odds: {chance:.1%} / {1 - chance:.1%} · Rarity only · Ranked ELO and XP unchanged")
        return embed

    async def publish_game(self, event, match, index):
        """Results are saved before sending. A restart publishes pending games without rerolling."""
        game = match["games"][index]
        if game.get("message_id"):
            return
        channel = await self.channel(event)
        embed = self.match_embed(event, match, index)
        cards = self.bot.get_cog("Cards")
        left, right = (event["entrants"][match[key]] for key in ("left", "right"))
        file = None
        try:
            image_slots = []
            for side, entrant in (("left", left), ("right", right)):
                slots = copy.deepcopy(entrant["slots"])
                if f"{side}_gold_rolls" in game:
                    cards.apply_ranked_gold_to_image_slots(slots, game[f"{side}_gold_rolls"])
                image_slots.append(slots)
            file = await asyncio.to_thread(cards.make_ranked_battle_file,
                image_slots[0], image_slots[1], f"tournament_{event['week']}_{match['id']}",
                seeded_name(event, match["left"], False), seeded_name(event, match["right"], False), event["game"], event["game"],
                title_font_size=24,
                user_power=game.get("left_power", left["power"]),
                opponent_power=game.get("right_power", right["power"]))
        except Exception:
            log.exception("Tournament artwork failed; posting the game summary without artwork")
        try:
            if file:
                embed.set_image(url=f"attachment://{file.filename}")
                message = await channel.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
            else:
                message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        finally:
            if file:
                file.close()
        game["message_id"] = message.id
        self.save()

    async def refresh_series(self, event, match):
        channel = await self.channel(event)
        embed = self.match_embed(event, match)
        if match.get("message_id"):
            try:
                message = await channel.fetch_message(match["message_id"])
                await message.edit(embed=embed, allowed_mentions=discord.AllowedMentions.none())
                match["dirty"] = False
                self.save()
                return
            except discord.NotFound:
                pass
        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        match["message_id"] = message.id
        match["dirty"] = False
        self.save()

    async def publish_daily_recaps(self, event):
        for day in engine.pending_daily_recaps(event):
            channel = await self.channel(event)
            buffer = await asyncio.to_thread(render_bracket, event, day)
            filename = f"tournament-bracket-{event['week']}-{day}.png"
            file = discord.File(buffer, filename=filename)
            embed = discord.Embed(title=f"{datetime.fromisoformat(day):%A} complete · Tournament bracket",
                                  description="All scheduled matches for this day are finished. Here is the updated bracket.",
                                  color=discord.Color.gold())
            embed.set_image(url=f"attachment://{filename}")
            try:
                message = await channel.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
            finally:
                file.close()
            event.setdefault("daily_recaps", {})[day] = message.id
            self.save()

    async def finish_tournament(self, event):
        if event["state"] != "complete" or any(
                not game.get("message_id") for match in event["matches"] for game in match["games"]):
            return
        if not event.get("prizes_paid"):
            if "prizes" not in event:
                event["prizes"] = engine.tournament_prizes(event)
                event["prize_receipt_key"] = f"tournament:{event['week']}:{event['channel_id']}:{event['game']}"
                self.save()
            users = self.bot.get_cog("Users")
            if not users:
                raise RuntimeError("Users must be loaded before tournament prizes can be awarded")
            users.award_tournament_prizes(event["prize_receipt_key"], event["prizes"])
            event["prizes_paid"] = True
            self.save()
        if event.get("prizes_message_id"):
            return
        channel = await self.channel(event)
        embed = discord.Embed(title="🏆 Tournament prizes awarded", color=discord.Color.gold())
        for prize in event["prizes"]:
            placement = {1: "1st", 2: "2nd", 3: "3rd–4th"}[prize["place"]]
            embed.add_field(name=f"{placement} · {seeded_name(event, prize['user_id'])}",
                            value=f"**{prize['gold']} gold + {prize['cash']} cash** added to their profile.", inline=False)
        message = await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        event["prizes_message_id"] = message.id
        self.save()

    def ensure_week(self, state, now):
        config = state["config"]
        local = now.astimezone(ZoneInfo(config["timezone"]))
        week = engine.week_start(now, config["timezone"]).isoformat()
        event = state.get("event")
        if event and ((event["state"] == "complete" and not event.get("prizes_message_id")) or
                engine.pending_daily_recaps(event) or event.get("board_dirty") or any(
                match.get("dirty") or any(not game.get("message_id") for game in match["games"])
                for match in event["matches"])):
            return False
        if local.weekday() > 2 or (event and (event["week"] >= week or event["state"] not in engine.TERMINAL)):
            return False
        if event:
            state.setdefault("history", []).append({"week": event["week"], "winner": event.get("winner"),
                                                     "teams": len(event["entrants"]), "state": event["state"]})
            state["history"] = state["history"][-12:]
        state["event"] = engine.new_event(config, now)
        self.save()
        return True

    async def tick(self, state, now):
        if not state.get("config", {}).get("enabled"):
            return
        created = self.ensure_week(state, now)
        event = state.get("event")
        if not event:
            return
        if created or not event.get("board_id"):
            await self.refresh_board(event)
        if self.close_registration(event, now):
            await self.refresh_board(event)
        # Retry saved but unpublished game results even after the final has ended.
        for match in event["matches"]:
            for index, game in enumerate(match["games"]):
                if not game.get("message_id"):
                    # Leave a full stage-specific interval after a recovered game is shown.
                    match["next_game_at"] = (now + timedelta(seconds=game_interval(match))).isoformat()
                    event["next_match_at"] = (now + timedelta(seconds=GAME_INTERVAL)).isoformat() if match["winner"] else match["next_game_at"]
                    self.save()
                    await self.publish_game(event, match, index)
                    await self.refresh_series(event, match)
                    await self.refresh_board(event)
                    await self.publish_daily_recaps(event)
                    await self.finish_tournament(event)
                    return
            if match.get("dirty"):
                await self.refresh_series(event, match)
        if event.get("board_dirty"):
            await self.refresh_board(event)
        await self.publish_daily_recaps(event)
        await self.finish_tournament(event)
        if event["state"] in engine.TERMINAL:
            return
        if event.get("next_match_at") and not engine.due(event["next_match_at"], now):
            return
        match = engine.next_match(event, now)
        if not match:
            return
        if event.get("active_match") == match["id"] and not engine.due(match["next_game_at"], now):
            return
        # Publish/check the series before drawing a result, so channel failures stop the broadcast.
        await self.refresh_series(event, match)
        if event.get("active_match") != match["id"]:
            event["active_match"] = match["id"]
            match["next_game_at"] = (now + timedelta(seconds=GAME_INTERVAL)).isoformat()
            self.save()
            # Weekend broadcasts reveal game one at the series start (5 p.m. for the first series).
            if game_interval(match) == GAME_INTERVAL:
                return
        gold = self.roll_game_gold(event, match)
        engine.play_game(event, match, now, game_interval(match))
        match["games"][-1].update(gold)
        if match["winner"]:
            # Keep the pause between separate series short; the 30-minute cadence is within a BO5.
            event["next_match_at"] = (now + timedelta(seconds=GAME_INTERVAL)).isoformat()
        self.save()
        await self.publish_game(event, match, len(match["games"]) - 1)
        await self.refresh_series(event, match)
        await self.refresh_board(event)
        await self.publish_daily_recaps(event)
        await self.finish_tournament(event)

    @tasks.loop(seconds=5)
    async def scheduler(self):
        for guild_id, state in list(self.data["guilds"].items()):
            async with self.lock(guild_id):
                try:
                    await self.tick(state, engine.utc_now())
                except Exception:
                    # Isolate each server; leave its saved results available for the next retry.
                    log.exception("Tournament scheduler failed for guild %s", guild_id)

    @scheduler.before_loop
    async def before_scheduler(self):
        await self.bot.wait_until_ready()

    @commands.group(invoke_without_command=True, aliases=["tourney"])
    @commands.guild_only()
    async def tournament(self, ctx):
        """Sign up Monday–Wednesday; show the bracket after signups close."""
        await self.tournament_response(ctx, "signup")

    async def tournament_response(self, ctx, action):
        async with self.lock(ctx.guild.id):
            state = self.guild_state(ctx.guild.id)
            config = state.get("config")
            if not config:
                await ctx.send("A moderator can enable weekly tournaments with `.tournament setup #tournament`.")
                return
            now = engine.utc_now()
            if config.get("enabled"):
                self.ensure_week(state, now)
            event = state.get("event")
            if not event:
                await ctx.send("The next tournament opens Monday. Signups run Monday–Wednesday.")
                return
            if config.get("enabled") and self.close_registration(event, now):
                await self.refresh_board(event)
            if event["state"] == "registration":
                message = self.register(state, ctx.author, leave=action == "leave", now=now)
                self.save()
                await ctx.send(message, embed=self.signup_embed(event), allowed_mentions=discord.AllowedMentions.none())
                await self.refresh_board(event)
                return
            if event["state"] == "cancelled":
                await ctx.send(event.get("reason", "This week's tournament was cancelled."))
                return
            day = now.astimezone(ZoneInfo(event["timezone"])).date().isoformat()
            buffer = await asyncio.to_thread(render_bracket, event, day, title="Tournament bracket")
            file = discord.File(buffer, filename=f"tournament-bracket-{event['week']}.png")
            embed = discord.Embed(title=f"{event['game']} tournament bracket", color=discord.Color.gold())
            embed.set_image(url=f"attachment://{file.filename}")
            try:
                await ctx.send(embed=embed, file=file, allowed_mentions=discord.AllowedMentions.none())
            finally:
                file.close()

    @tournament.command(name="help")
    async def tournament_help(self, ctx):
        await ctx.send(
            "**Weekly tournament commands**\n"
            "`.tournament` — sign up Monday–Wednesday and show players; after Wednesday, show the bracket image\n"
            "`.tournament join` — alias for the signup flow\n"
            "`.tournament leave` — withdraw before Wednesday ends\n"
            "**Moderators**\n"
            "`.tournament setup #channel [lol|valorant] [timezone] [HH:MM]` — enable weekly scheduling; "
            "defaults: LoL, America/New_York, 17:00\n"
            "With no channel supplied, setup finds `#tournament` in this server.\n"
            "`.tournament channel #channel` — move the broadcast\n"
            "`.tournament pause` / `.tournament resume` — pause/resume scheduling and games\n"
            "`.tournament cancel` — cancel this week's event\n"
            "Signups: Monday 00:00 through Wednesday 23:59 Eastern by default. "
            "All games are BO5, rarity only, with free entry. Prizes: 1st 20 gold + 500 cash; "
            "2nd 10 gold + 250 cash; each semifinal loser 5 gold + 150 cash.")

    @tournament.command(name="setup")
    async def tournament_setup(self, ctx, channel: discord.TextChannel = None, game: str = "lol",
                               timezone_name: str = DEFAULT_TIMEZONE, match_time: str = DEFAULT_MATCH_TIME):
        await self.moderator(ctx)
        if channel is None:
            candidates = [item for item in ctx.guild.text_channels if item.name.lower() == "tournament"]
            if len(candidates) != 1:
                await ctx.send("Create a text channel named `tournament`, then run `.tournament setup`, "
                               "or specify the channel with `.tournament setup #tournament`.")
                return
            channel = candidates[0]
        aliases = {"lol": GAME_LEAGUE, "league": GAME_LEAGUE, "valorant": GAME_VALORANT, "val": GAME_VALORANT}
        if game.lower() not in aliases:
            await ctx.send("Choose `lol` or `valorant`.")
            return
        try:
            ZoneInfo(timezone_name)
            engine.parse_time(match_time)
        except (ZoneInfoNotFoundError, ValueError):
            await ctx.send("Use an IANA timezone such as `America/New_York` and a 24-hour time such as `17:00`.")
            return
        permissions = channel.permissions_for(ctx.guild.me)
        if channel.guild.id != ctx.guild.id or not all((permissions.view_channel, permissions.send_messages,
                                                       permissions.embed_links, permissions.attach_files,
                                                       permissions.read_message_history)):
            await ctx.send("Use a channel in this server where I can view, send messages, embed links, attach files, and read message history.")
            return
        async with self.lock(ctx.guild.id):
            state = self.guild_state(ctx.guild.id)
            event = state.get("event")
            if event and event["state"] not in engine.TERMINAL:
                await ctx.send("A tournament is already open. Use `.tournament channel`, `.tournament pause`, "
                               "or `.tournament cancel` before changing the weekly format.")
                return
            state["config"] = {"channel_id": channel.id, "game": aliases[game.lower()],
                               "timezone": timezone_name, "match_time": match_time, "enabled": True}
            self.save()
            self.ensure_week(state, engine.utc_now())
            if state.get("event") and state["event"]["state"] == "registration":
                await self.refresh_board(state["event"])
        await ctx.send(f"Weekly tournaments enabled in {channel.mention}. Signups: Monday–Wednesday, "
                       f"closing Thursday 00:00. Matches: Thursday–Sunday at {match_time} ({timezone_name}). "
                       "If signups have already closed, the next event opens Monday.")

    async def command_register(self, ctx, leave):
        await self.tournament_response(ctx, "leave" if leave else "signup")

    @tournament.command(name="join")
    async def tournament_join(self, ctx):
        await self.command_register(ctx, leave=False)

    @tournament.command(name="leave")
    async def tournament_leave(self, ctx):
        await self.command_register(ctx, leave=True)

    @tournament.command(name="channel")
    async def tournament_channel(self, ctx, channel: discord.TextChannel):
        await self.moderator(ctx)
        permissions = channel.permissions_for(ctx.guild.me)
        if channel.guild.id != ctx.guild.id or not all((permissions.view_channel, permissions.send_messages,
                                                       permissions.embed_links, permissions.attach_files,
                                                       permissions.read_message_history)):
            await ctx.send("Use a channel in this server with view, send, embed, attach, and history permissions.")
            return
        async with self.lock(ctx.guild.id):
            state = self.guild_state(ctx.guild.id)
            if not state.get("config"):
                await ctx.send("Run `.tournament setup` first.")
                return
            state["config"]["channel_id"] = channel.id
            event = state.get("event")
            if event:
                event["channel_id"] = channel.id
                event["board_id"] = None
                for match in event["matches"]:
                    match["message_id"] = None
            self.save()
            if event:
                await self.refresh_board(event)
        await ctx.send(f"Tournament broadcast moved to {channel.mention}.")

    async def set_enabled(self, ctx, enabled):
        await self.moderator(ctx)
        async with self.lock(ctx.guild.id):
            state = self.guild_state(ctx.guild.id)
            if not state.get("config"):
                await ctx.send("Run `.tournament setup` first.")
                return
            state["config"]["enabled"] = enabled
            self.save()
        await ctx.send("Tournament scheduling resumed. Overdue games will continue at the normal broadcast pace."
                       if enabled else "Tournament scheduling paused. Saved scores and brackets are preserved; signup deadlines remain unchanged.")

    @tournament.command(name="pause")
    async def tournament_pause(self, ctx):
        await self.set_enabled(ctx, False)

    @tournament.command(name="resume")
    async def tournament_resume(self, ctx):
        await self.set_enabled(ctx, True)

    @tournament.command(name="cancel")
    async def tournament_cancel(self, ctx):
        await self.moderator(ctx)
        async with self.lock(ctx.guild.id):
            state = self.guild_state(ctx.guild.id)
            event = state.get("event")
            if not event or event["state"] in engine.TERMINAL:
                await ctx.send("There is no active tournament to cancel.")
                return
            active = event.get("active_match")
            event["state"] = "cancelled"
            event["active_match"] = None
            event["reason"] = "Cancelled by a moderator. Next week's signups still open Monday."
            event["board_dirty"] = True
            self.save()
            if active:
                await self.refresh_series(event, next(match for match in event["matches"] if match["id"] == active))
            await self.refresh_board(event)
        await ctx.send("This week's tournament was cancelled.")


async def setup(bot):
    await bot.add_cog(Tournaments(bot))
