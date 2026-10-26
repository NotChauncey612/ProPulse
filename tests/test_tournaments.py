import copy
import json
import random
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image
import discord
from discord.ext import commands

from classes import tournament_engine as engine
from classes.cards import Cards, GAME_LEAGUE, GAME_VALORANT, RARITY_TEAM_MULTIPLIERS
from classes.users import LeaderboardView, Users
from classes.tournaments import FINALS_GAME_INTERVAL, GAME_INTERVAL, Tournaments, game_interval
from classes.tournament_bracket import estimated_match_times, render_bracket


MONDAY = datetime(2026, 10, 19, 12, tzinfo=timezone.utc)
CONFIG = {"game": GAME_LEAGUE, "timezone": "America/New_York", "match_time": "17:00",
          "channel_id": 123, "enabled": True}


def entrant(name="Team", power=625):
    return {"name": name, "power": power, "slots": [
        {"role": role, "rarity": "Silver", "player_name": f"Player {role}"}
        for role in ("TOP", "JNG", "MID", "BOT", "SUP")]}


def event_with(count):
    event = engine.new_event(CONFIG, MONDAY)
    event["entrants"] = {str(i + 1): entrant(f"Team {i + 1}") for i in range(count)}
    return event


class TournamentRulesTests(unittest.TestCase):
    def test_every_supported_field_finishes_with_one_champion(self):
        sunday = datetime(2026, 10, 26, tzinfo=timezone.utc)
        for count in range(2, 33):
            with self.subTest(count=count):
                event = event_with(count)
                rng = random.Random(42)
                engine.lock_registration(event, rng)
                for _ in range(1000):
                    match = engine.next_match(event, sunday)
                    if match is None:
                        break
                    engine.play_game(event, match, sunday, GAME_INTERVAL, rng)
                self.assertEqual(event["state"], "complete")
                self.assertIn(event["winner"], event["entrants"])
                finals = [m for m in event["matches"] if m["stage"] == "final"]
                self.assertEqual(len(finals), 1)
                for match in event["matches"]:
                    if match.get("bye"):
                        self.assertEqual(match["games"], [])
                    else:
                        self.assertIn(len(match["games"]), (3, 4, 5))
                        self.assertEqual(max(engine.series_score(match)), 3)

    def test_groups_play_every_pair_and_exactly_two_qualify(self):
        for count in (12, 13, 16, 25, 32):
            event = event_with(count)
            engine.lock_registration(event, random.Random(3))
            for group, members in event["groups"].items():
                matches = [m for m in event["matches"] if m["group"] == group]
                self.assertEqual(len(matches), len(members) * (len(members) - 1) // 2)
                pairs = {frozenset((m["left"], m["right"])) for m in matches}
                self.assertEqual(len(pairs), len(matches))
            thursday = datetime.fromisoformat(event["stage_times"]["groups"])
            while match := engine.next_match(event, thursday):
                engine.play_game(event, match, thursday, GAME_INTERVAL, random.Random(match["id"]))
            quarters = [m for m in event["matches"] if m["stage"] == "quarterfinal"]
            self.assertEqual(len(quarters), 4)
            qualified = {uid for m in quarters for uid in (m["left"], m["right"])}
            for group in event["groups"]:
                expected = {row["uid"] for row in engine.standings(event, group)[:2]}
                self.assertEqual(expected, qualified.intersection(event["groups"][group]))
            # Group opponents occupy opposite halves of the knockout bracket.
            half_a = {uid for m in quarters[:2] for uid in (m["left"], m["right"])}
            for group in event["groups"]:
                self.assertEqual(len(half_a.intersection(event["groups"][group])), 1)

    def test_schedule_and_daylight_saving(self):
        event = event_with(8)
        self.assertEqual(event["closes_at"], "2026-10-22T04:00:00+00:00")
        self.assertEqual(event["stage_times"]["quarterfinal"], "2026-10-23T21:00:00+00:00")
        winter = engine.new_event(CONFIG, datetime(2026, 11, 9, 12, tzinfo=timezone.utc))
        self.assertEqual(winter["stage_times"]["final"], "2026-11-15T22:00:00+00:00")
        engine.lock_registration(event, random.Random(1))
        friday = datetime.fromisoformat(event["stage_times"]["quarterfinal"])
        self.assertIsNone(engine.next_match(event, friday - timedelta(seconds=1)))
        while match := engine.next_match(event, friday):
            engine.play_game(event, match, friday, GAME_INTERVAL, random.Random(2))
        self.assertEqual(len([m for m in event["matches"] if m["stage"] == "semifinal"]), 2)
        self.assertIsNone(engine.next_match(event, friday))

    def test_byes_and_low_turnout(self):
        for count in (0, 1):
            event = event_with(count)
            engine.lock_registration(event)
            self.assertEqual(event["state"], "cancelled")
        for count in (9, 10, 11):
            event = event_with(count)
            engine.lock_registration(event, random.Random(4))
            self.assertEqual(sum(not m.get("bye", False) for m in event["matches"]), count - 8)
            self.assertTrue(all(m["stage"] == "preliminary" for m in event["matches"]))

    def test_small_fields_wait_until_their_scheduled_day(self):
        for count in range(2, 12):
            with self.subTest(entrants=count):
                event = event_with(count)
                engine.lock_registration(event, random.Random(4))
                stage = "final" if count == 2 else "semifinal" if count <= 4 else "quarterfinal" if count <= 8 else "preliminary"
                start = datetime.fromisoformat(event["stage_times"][stage])
                expected_day = 6 if count == 2 else 5 if count <= 4 else 4
                self.assertEqual(start.astimezone(engine.ZoneInfo(CONFIG["timezone"])).weekday(), expected_day)
                for day in range(3, expected_day):
                    earlier = datetime.fromisoformat(event["stage_times"]["groups"]) + timedelta(days=day - 3)
                    self.assertIsNone(engine.next_match(event, earlier))
                self.assertIsNone(engine.next_match(event, start - timedelta(seconds=1)))
                self.assertIsNotNone(engine.next_match(event, start))

    def test_all_required_byes_go_to_highest_ranked_entrants(self):
        for count in (3, 5, 6, 7, 9, 10, 11):
            for seed in range(5):
                with self.subTest(entrants=count, draw=seed):
                    event = event_with(count)
                    ranking = list(reversed(list(event["entrants"])))
                    engine.lock_registration(event, random.Random(seed), leaderboard_order=ranking)
                    bye_count = (1 << (count - 1).bit_length()) - count
                    recipients = {match["winner"] for match in event["matches"] if match.get("bye")}
                    self.assertEqual(recipients, set(ranking[:bye_count]))
                    self.assertEqual(event["bye_recipients"], ranking[:bye_count])
                    self.assertTrue(all(not match["games"] for match in event["matches"]))

    def test_every_entrant_gets_a_leaderboard_seed_and_seeded_pairings(self):
        for count in (2, 4, 8, 12, 13, 32):
            event = event_with(count)
            ranking = list(reversed(list(event["entrants"])))
            engine.lock_registration(event, random.Random(7), leaderboard_order=ranking)
            self.assertEqual(event["seeds"], ranking)
            self.assertEqual(event["tournament_seeds"], {uid: i for i, uid in enumerate(ranking, 1)})
            if count == 8:
                pairs = {(event["tournament_seeds"][m["left"]], event["tournament_seeds"][m["right"]]) for m in event["matches"]}
                self.assertEqual(pairs, {(1, 8), (4, 5), (2, 7), (3, 6)})
        event = event_with(16)
        engine.lock_registration(event, leaderboard_order=list(event["entrants"]))
        totals = [sum(event["tournament_seeds"][uid] for uid in members) for members in event["groups"].values()]
        self.assertEqual(totals, [34, 34, 34, 34])

    def test_five_game_series_and_rarity_odds(self):
        event = event_with(2)
        engine.lock_registration(event, random.Random(1))
        match = event["matches"][0]
        rng = SimpleNamespace(random=iter([0.1, 0.9, 0.1, 0.9, 0.1]).__next__)
        for _ in range(5):
            engine.play_game(event, match, MONDAY, GAME_INTERVAL, rng)
        self.assertEqual(engine.series_score(match), [3, 2])
        with self.assertRaises(ValueError):
            engine.play_game(event, match, MONDAY, GAME_INTERVAL, rng)
        self.assertAlmostEqual(engine.win_chance(1125, 625), 0.7641509433962265)
        self.assertEqual(engine.win_chance(625, 625), 0.5)


class TournamentIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict("os.environ", {"DATA_DIR": self.temp.name})
        self.env.start()
        self.bot = SimpleNamespace(get_cog=lambda name: self.cogs.get(name))
        self.cogs = {}
        self.cog = Tournaments(self.bot)
        async def mark_recaps(event):
            for day in engine.pending_daily_recaps(event):
                event.setdefault("daily_recaps", {})[day] = 999
        self.cog.publish_daily_recaps = AsyncMock(side_effect=mark_recaps)
        async def mark_prizes(event):
            if event["state"] == "complete":
                event.update(prizes_paid=True, prizes_message_id=888)
        self.cog.finish_tournament = AsyncMock(side_effect=mark_prizes)
        self.cards = Cards(self.bot)
        self.users = SimpleNamespace(users={}, get_leaderboard_entries=lambda: [
            {"user_id": uid} for uid in self.users.users])
        self.cogs.update(Cards=self.cards, Users=self.users)

    async def asyncTearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def make_profile(self, game=GAME_LEAGUE, rarity="Silver"):
        roles = self.cards.get_team_roles(game)
        definitions = [next(card for card in self.cards.cards.values()
                            if self.cards.card_game(card) == game and self.cards.card_can_fill_team_role(card, role, game))
                       for role in roles]
        profile = {"xp": 999999, "level": 200, "cash": 123, "elo": 2200, "cards": [],
                   "team_stats": {role: 99999 for role in roles}, "teams": {game: {}}}
        for i, (role, card) in enumerate(zip(roles, definitions)):
            instance = {"instance_id": f"instance-{i}", "card_id": card["card_id"], "rarity": rarity}
            profile["cards"].append(instance)
            profile["teams"][game][role] = instance["instance_id"]
        self.users.users["1"] = profile
        return profile

    async def test_real_team_snapshots_ignore_progression_and_never_mutate_users(self):
        for game in (GAME_LEAGUE, GAME_VALORANT):
            for rarity, multiplier in RARITY_TEAM_MULTIPLIERS.items():
                profile = self.make_profile(game, rarity)
                before = copy.deepcopy(profile)
                result = self.cog.snapshot("1", game, "Entrant")
                self.assertEqual(result["power"], 5 * round(100 * multiplier))
                self.assertEqual(profile, before)
                self.assertTrue(all(slot["completion_multiplier"] == 1 for slot in result["slots"]))
                profile["cards"][0]["rarity"] = "Unknown"
                self.assertEqual(result["slots"][0]["rarity"], rarity)
                with self.assertRaises(ValueError):
                    self.cog.snapshot("1", game, "Entrant")

    async def test_discord_extension_loads_and_all_subcommands_reject_dms(self):
        async with commands.Bot(command_prefix=".", intents=discord.Intents.none()) as bot:
            await bot.load_extension("classes.tournaments")
            cog = bot.get_cog("Tournaments")
            self.assertIsNotNone(bot.get_command("tournament setup"))
            self.assertIsNotNone(bot.get_command("tournament join"))
            self.assertEqual(len(bot.persistent_views), 1)
            with self.assertRaises(commands.NoPrivateMessage):
                await cog.cog_check(SimpleNamespace(guild=None))
            self.assertTrue(await cog.cog_check(SimpleNamespace(guild=SimpleNamespace(id=123))))
            await bot.unload_extension("classes.tournaments")
            self.assertEqual(len(bot.persistent_views), 0)

    async def test_registration_deadline_duplicates_cap_and_withdrawal(self):
        self.make_profile()
        event = event_with(0)
        state = {"config": CONFIG.copy(), "event": event}
        member = SimpleNamespace(id=1, display_name="Entrant", bot=False)
        self.assertIn("signed up", self.cog.register(state, member, now=MONDAY))
        self.cog.register(state, member, now=MONDAY)
        self.assertEqual(len(event["entrants"]), 1)
        close = datetime.fromisoformat(event["closes_at"])
        self.assertIn("Signups are open", self.cog.register(state, member, leave=True, now=close))
        self.assertIn("withdrew", self.cog.register(state, member, leave=True, now=close - timedelta(seconds=1)))
        event["entrants"] = {str(i + 2): entrant() for i in range(32)}
        self.assertIn("full", self.cog.register(state, member, now=MONDAY))

    async def test_bare_command_signs_up_once_and_lists_players_in_seed_order(self):
        profile = self.make_profile()
        self.users.users.update({"2": copy.deepcopy(profile), "3": copy.deepcopy(profile)})
        state = {"config": CONFIG.copy(), "event": event_with(0)}
        self.cog.data = {"guilds": {"123": state}}
        self.cog.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
        names = {1: "Chauncey", 2: "Kai", 3: "Jordan"}
        with patch.object(engine, "utc_now", return_value=MONDAY), patch.object(self.cog, "snapshot", wraps=self.cog.snapshot) as snapshots:
            for uid in (3, 1, 2):
                ctx = SimpleNamespace(guild=SimpleNamespace(id=123),
                                      author=SimpleNamespace(id=uid, display_name=names[uid], bot=False), send=AsyncMock())
                await Tournaments.tournament.callback(self.cog, ctx)
            self.assertEqual(ctx.send.await_args.kwargs["embed"].description,
                             "**#1** Chauncey\n**#2** Kai\n**#3** Jordan")
            await Tournaments.tournament.callback(self.cog, ctx)
            self.assertIn("already signed up", ctx.send.await_args.args[0])
            self.assertEqual(snapshots.call_count, 3)
        self.assertEqual(len(state["event"]["entrants"]), 3)
        self.assertNotIn("tournament_seeds", state["event"])

    async def test_bare_command_after_deadline_draws_bracket_without_entering_or_playing(self):
        profile = self.make_profile()
        self.users.users["2"] = copy.deepcopy(profile)
        event = event_with(2)
        state = {"config": CONFIG.copy(), "event": event}
        self.cog.data = {"guilds": {"123": state}}
        self.cog.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
        captured = []
        async def send(**kwargs):
            captured.append(kwargs["file"].fp.read(8))
        ctx = SimpleNamespace(guild=SimpleNamespace(id=123), author=SimpleNamespace(id=3), send=AsyncMock(side_effect=send))
        with patch.object(engine, "utc_now", return_value=datetime.fromisoformat(event["closes_at"])):
            await Tournaments.tournament.callback(self.cog, ctx)
            await Tournaments.tournament.callback(self.cog, ctx)
        self.assertEqual(captured, [b"\x89PNG\r\n\x1a\n"] * 2)
        self.assertEqual(len(event["entrants"]), 2)
        self.assertEqual(event["state"], "playoffs")
        self.assertTrue(all(not match["games"] for match in event["matches"]))

    async def test_bye_ranking_matches_real_leaderboard_without_mutating_profiles(self):
        users = Users.__new__(Users)
        users.bot = None
        users.users = {
            "1": {"elo": 1200, "discord_username": "Zed"},
            "2": {"elo": 1400, "discord_username": "Bea"},
            "3": {"elo": 1400, "discord_username": "Ada"},
            "4": {"elo": 1600, "discord_username": "Not Entered"},
        }
        self.cogs["Users"] = users
        before = copy.deepcopy(users.users)
        event = event_with(3)
        ranking = self.cog.leaderboard_seed_order(event)
        self.assertEqual(ranking, ["3", "2", "1"])
        self.assertEqual(event["leaderboard_positions"], {"1": 4, "2": 3, "3": 2})
        self.assertEqual(users.users, before)
        engine.lock_registration(event, random.Random(9), leaderboard_order=ranking)
        users.users["1"]["elo"] = 3000
        reloaded = json.loads(json.dumps(event))
        self.assertEqual(reloaded["bye_recipients"], ["3"])
        self.assertEqual(reloaded["leaderboard_positions"]["3"], 2)
        self.assertEqual(reloaded["tournament_seeds"], {"3": 1, "2": 2, "1": 3})

    async def test_seeds_appear_in_matches_and_guild_leaderboard_without_changing(self):
        event = event_with(4)
        ranking = ["3", "1", "4", "2"]
        engine.lock_registration(event, leaderboard_order=ranking)
        self.cog.data = {"guilds": {"123": {"config": CONFIG.copy(), "event": event}}}
        with patch.object(engine, "utc_now", return_value=MONDAY + timedelta(days=4)):
            seeds = self.cog.seeds_for_guild(123)
            self.assertEqual(seeds, {"3": 1, "1": 2, "4": 3, "2": 4})
            self.assertEqual(self.cog.seeds_for_guild(456), {})
        entries = [{"user_id": uid, "ign": f"Person {uid}", "elo": 3000 - i,
                    "rank": "Gold", "total_power": 100} for i, uid in enumerate(["1", "5", "3"])]
        view = LeaderboardView(self.users, entries, tournament_seeds=seeds)
        lines = view.build_embed().description.splitlines()
        self.assertIn("Tournament seed: `#2`", lines[0])
        self.assertNotIn("Tournament seed", lines[1])
        self.assertIn("Tournament seed: `#1`", lines[2])
        match = event["matches"][0]
        intro = self.cog.match_embed(event, match)
        for uid in (match["left"], match["right"]):
            self.assertIn(f"#{seeds[uid]} {event['entrants'][uid]['name']}", intro.title)
        engine.play_game(event, match, MONDAY, FINALS_GAME_INTERVAL, SimpleNamespace(random=lambda: 0))
        game = self.cog.match_embed(event, match, 0)
        self.assertIn(f"#{seeds[match['left']]} {event['entrants'][match['left']]['name']}", game.description)
        with patch.object(engine, "utc_now", return_value=MONDAY + timedelta(days=7)):
            self.assertEqual(self.cog.seeds_for_guild(123), {})

    async def test_lineups_lock_once_and_new_week_needs_new_signup(self):
        profile = self.make_profile()
        state = {"config": CONFIG.copy(), "event": event_with(2)}
        state["event"]["board_id"] = 99
        self.users.users["2"] = copy.deepcopy(profile)
        self.cog.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
        close = datetime.fromisoformat(state["event"]["closes_at"])
        profile["cards"][0]["rarity"] = "Challenger"
        await self.cog.tick(state, close)
        frozen = copy.deepcopy(state["event"]["entrants"])
        profile["cards"][0]["rarity"] = "Silver"
        await self.cog.tick(state, close + timedelta(hours=1))
        self.assertEqual(state["event"]["entrants"], frozen)
        state["event"]["state"] = "complete"
        state["event"].update(prizes_paid=True, prizes_message_id=888)
        next_monday = MONDAY + timedelta(days=7)
        self.assertTrue(self.cog.ensure_week(state, next_monday))
        self.assertEqual(state["event"]["entrants"], {})
        self.assertEqual(len(state["history"]), 1)

    async def test_restart_replays_unpublished_result_without_reroll(self):
        event = event_with(2)
        event["board_id"] = 99
        engine.lock_registration(event, random.Random(0))
        match = event["matches"][0]
        engine.play_game(event, match, MONDAY, GAME_INTERVAL, random.Random(0))
        self.cog.data = {"guilds": {"123": {"config": CONFIG.copy(), "event": event}}}
        self.cog.save()
        restarted = Tournaments(self.bot)
        state = restarted.guild_state("123")
        restarted.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
        restarted.refresh_series = AsyncMock()
        async def published(e, m, i):
            m["games"][i]["message_id"] = 101
        restarted.publish_game = AsyncMock(side_effect=published)
        sunday = datetime(2026, 10, 26, tzinfo=timezone.utc)
        with patch.object(engine, "play_game", side_effect=AssertionError("must not reroll")):
            await restarted.tick(state, sunday)
        restarted.publish_game.assert_awaited_once()
        self.assertEqual(state["event"]["matches"][0]["games"][0]["winner"], match["games"][0]["winner"])

    async def test_scheduler_full_week_durable_game_posts_and_pacing(self):
        event = event_with(2)
        engine.lock_registration(event, random.Random(0))
        event["board_id"] = 99
        state = {"config": CONFIG.copy(), "event": event}
        self.cog.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
        self.cog.refresh_series = AsyncMock(side_effect=lambda e, m: m.update(dirty=False, message_id=50))
        published = []
        async def publish(e, m, i):
            # The outcome exists on disk before any Discord send.
            saved = json.loads((Path(self.temp.name) / "tournaments.json").read_text())
            self.assertEqual(len(saved["guilds"]["123"]["event"]["matches"][0]["games"]), i + 1)
            m["games"][i]["message_id"] = 100 + i
            published.append(i)
        self.cog.publish_game = AsyncMock(side_effect=publish)
        self.cog.data = {"guilds": {"123": state}}
        now = datetime.fromisoformat(event["stage_times"]["final"])
        await self.cog.tick(state, now)
        self.assertEqual(published, [0])  # Sunday's first game appears at the series start.
        await self.cog.tick(state, now + timedelta(seconds=1799))
        self.assertEqual(published, [0])
        for _ in range(5):
            now += timedelta(minutes=30)
            await self.cog.tick(state, now)
            if event["state"] == "complete":
                break
        self.assertEqual(event["state"], "complete")
        self.assertIn(len(published), (3, 4, 5))
        self.assertEqual(len(set(published)), len(published))

    async def test_ranked_gold_format_changes_per_game_and_survives_recovery(self):
        event = event_with(2)
        engine.lock_registration(event, leaderboard_order=list(event["entrants"]))
        match = event["matches"][0]
        with patch("classes.cards.random.uniform", return_value=200):
            gold = self.cog.roll_game_gold(event, match)
        engine.play_game(event, match, MONDAY, FINALS_GAME_INTERVAL, SimpleNamespace(random=lambda: 0))
        match["games"][-1].update(gold)
        self.assertEqual(gold["left_gold"], 125000)
        self.assertEqual(sum(role["gold"] for role in gold["left_gold_rolls"].values()), 125000)
        self.cog.data = {"guilds": {"123": {"config": CONFIG.copy(), "event": event}}}
        self.cog.save()
        restarted = Tournaments(self.bot)
        recovered = restarted.guild_state("123")["event"]
        recovered_match = recovered["matches"][0]
        channel = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=998)))
        self.bot.get_channel = lambda channel_id: channel
        with patch.object(self.cards, "roll_ranked_gold_from_stats", side_effect=AssertionError("must reuse saved gold")), \
                patch.object(self.cards, "make_ranked_battle_file", return_value=None) as artwork:
            await restarted.publish_game(recovered, recovered_match, 0)
        posted = channel.send.await_args.kwargs["embed"]
        fields = {field.name: field.value for field in posted.fields}
        self.assertNotIn("⚔️ Power", fields)
        self.assertNotIn("🪙 Gold", fields)
        self.assertEqual(set(fields), {"Games so far"})
        self.assertEqual(artwork.call_args.kwargs["user_power"], 625)
        self.assertEqual(artwork.call_args.kwargs["opponent_power"], 625)
        self.assertEqual(recovered_match["games"][0]["left_gold"], 125000)
        with patch("classes.cards.random.uniform", return_value=300):
            next_gold = self.cog.roll_game_gold(event, match)
        engine.play_game(event, match, MONDAY + timedelta(minutes=30), FINALS_GAME_INTERVAL,
                         SimpleNamespace(random=lambda: 0))
        match["games"][-1].update(next_gold)
        self.assertEqual(match["games"][0]["left_gold"], 125000)
        self.assertEqual(match["games"][1]["left_gold"], 187500)

    async def test_round_specific_cadence_and_public_display_names(self):
        for count, stage in ((16, "groups"), (9, "preliminary"), (8, "quarterfinal"),
                             (4, "semifinal"), (2, "final")):
            with self.subTest(stage=stage):
                event = event_with(count)
                event["entrants"]["1"]["name"] = "Kai"
                event["entrants"]["2"]["name"] = "Riley"
                engine.lock_registration(event, random.Random(0))
                match = next(m for m in event["matches"] if not m.get("bye"))
                self.assertEqual(match["stage"], stage)
                expected = 1800 if stage in {"semifinal", "final"} else 30
                self.assertEqual(game_interval(match), expected)
                state = {"config": CONFIG.copy(), "event": event}
                event["board_id"] = 99
                self.cog.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
                self.cog.refresh_series = AsyncMock(side_effect=lambda e, m: m.update(dirty=False, message_id=50))
                async def publish(e, m, i):
                    m["games"][i]["message_id"] = 100 + i
                self.cog.publish_game = AsyncMock(side_effect=publish)
                now = datetime.fromisoformat(event["stage_times"][stage])
                await self.cog.tick(state, now)
                if expected == 30:
                    self.assertEqual(match["games"], [])
                    now += timedelta(seconds=30)
                    await self.cog.tick(state, now)
                self.assertEqual(len(match["games"]), 1)
                await self.cog.tick(state, now + timedelta(seconds=expected - 1))
                self.assertEqual(len(match["games"]), 1)
                await self.cog.tick(state, now + timedelta(seconds=expected))
                self.assertEqual(len(match["games"]), 2)
                intro = self.cog.match_embed(event, match)
                game = self.cog.match_embed(event, match, 1)
                for uid in (match["left"], match["right"]):
                    self.assertIn(event["entrants"][uid]["name"], intro.title)
                    self.assertIn(event["entrants"][uid]["name"], game.description)
                self.assertNotIn("your team", str(game.to_dict()).lower())
                self.assertNotIn("Series #", intro.title)
                self.assertNotIn("Next game:", game.description)
                self.assertNotIn("The series continues", game.description)

    async def test_recovered_weekend_game_waits_thirty_minutes(self):
        event = event_with(2)
        engine.lock_registration(event, random.Random(0))
        match = event["matches"][0]
        now = datetime.fromisoformat(event["stage_times"]["final"])
        engine.play_game(event, match, now, FINALS_GAME_INTERVAL, random.Random(0))
        event["active_match"] = match["id"]
        event["board_id"] = 99
        state = {"config": CONFIG.copy(), "event": event}
        self.cog.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
        self.cog.refresh_series = AsyncMock(side_effect=lambda e, m: m.update(dirty=False, message_id=50))
        async def publish(e, m, i):
            m["games"][i]["message_id"] = 100 + i
        self.cog.publish_game = AsyncMock(side_effect=publish)
        restart = now + timedelta(hours=2)
        await self.cog.tick(state, restart)
        self.assertEqual(datetime.fromisoformat(match["next_game_at"]), restart + timedelta(minutes=30))
        await self.cog.tick(state, restart + timedelta(minutes=29, seconds=59))
        self.assertEqual(len(match["games"]), 1)
        await self.cog.tick(state, restart + timedelta(minutes=30))
        self.assertEqual(len(match["games"]), 2)

    async def test_failed_final_publication_is_retried_before_new_week(self):
        event = event_with(2)
        engine.lock_registration(event, random.Random(0))
        match = event["matches"][0]
        for _ in range(3):
            engine.play_game(event, match, MONDAY, GAME_INTERVAL, SimpleNamespace(random=lambda: 0))
        event["board_id"] = 99
        for game in match["games"][:-1]:
            game["message_id"] = 5
        state = {"config": CONFIG.copy(), "event": event}
        self.cog.refresh_board = AsyncMock(side_effect=lambda e: e.update(board_dirty=False))
        self.cog.refresh_series = AsyncMock(side_effect=lambda e, m: m.update(dirty=False))
        self.cog.publish_game = AsyncMock(side_effect=RuntimeError("Discord temporarily unavailable"))
        next_monday = MONDAY + timedelta(days=7)
        with self.assertRaises(RuntimeError):
            await self.cog.tick(state, next_monday)
        self.assertIs(state["event"], event)
        self.assertEqual(len(match["games"]), 3)
        async def publish(e, m, i):
            m["games"][i]["message_id"] = 6
        self.cog.publish_game = AsyncMock(side_effect=publish)
        await self.cog.tick(state, next_monday)
        await self.cog.tick(state, next_monday)
        self.assertEqual(state["event"]["entrants"], {})

    async def test_pause_and_missing_channel_do_not_draw_games(self):
        event = event_with(2)
        engine.lock_registration(event)
        state = {"config": dict(CONFIG, enabled=False), "event": event}
        sunday = datetime(2026, 10, 26, tzinfo=timezone.utc)
        self.cog.refresh_board = AsyncMock(side_effect=RuntimeError("missing channel"))
        await self.cog.tick(state, sunday)
        state["config"]["enabled"] = True
        with self.assertRaises(RuntimeError):
            await self.cog.tick(state, sunday)
        self.assertEqual(event["matches"][0]["games"], [])

    async def test_discord_embed_limits_at_maximum_field(self):
        event = event_with(32)
        # Use actual maximum snowflake lengths for Discord mention sizes.
        event["entrants"] = {str(1000000000000000000 + i): entrant("x" * 100) for i in range(32)}
        self.users.users = {uid: {} for uid in event["entrants"]}
        embed = self.cog.board_embed(event)
        self.assertLessEqual(len(embed), 6000)
        engine.lock_registration(event, random.Random(0))
        sunday = datetime(2026, 10, 26, tzinfo=timezone.utc)
        while match := engine.next_match(event, sunday):
            engine.play_game(event, match, sunday, GAME_INTERVAL, random.Random(match["id"]))
        embed = self.cog.board_embed(event)
        self.assertLessEqual(len(embed), 6000)
        for field in embed.fields:
            self.assertLessEqual(len(field.value), 1024)
        for match in event["matches"]:
            game_embed = self.cog.match_embed(event, match, len(match["games"]) - 1)
            self.assertLessEqual(len(game_embed), 6000)

    async def test_daily_bracket_posts_to_configured_channel_and_survives_restart(self):
        event = event_with(2)
        engine.lock_registration(event, random.Random(0))
        match = event["matches"][0]
        now = datetime.fromisoformat(event["stage_times"]["final"])
        for _ in range(3):
            engine.play_game(event, match, now, FINALS_GAME_INTERVAL, SimpleNamespace(random=lambda: 0))
            match["games"][-1]["message_id"] = 101
        self.cog.data = {"guilds": {"123": {"config": CONFIG.copy(), "event": event}}}
        captured = []
        async def send(**kwargs):
            self.assertEqual(kwargs["file"].fp.read(8), b"\x89PNG\r\n\x1a\n")
            captured.append(kwargs["embed"].title)
            return SimpleNamespace(id=555)
        channel = SimpleNamespace(send=AsyncMock(side_effect=send))
        self.bot.get_channel = lambda channel_id: channel if channel_id == CONFIG["channel_id"] else None
        await Tournaments.publish_daily_recaps(self.cog, event)
        self.assertEqual(captured, ["Sunday complete · Tournament bracket"])
        restarted = Tournaments(self.bot)
        await restarted.publish_daily_recaps(restarted.guild_state("123")["event"])
        channel.send.assert_awaited_once()

    async def test_failed_bracket_send_retries_and_blocks_week_rollover(self):
        event = event_with(2)
        engine.lock_registration(event, random.Random(0))
        match = event["matches"][0]
        for _ in range(3):
            engine.play_game(event, match, MONDAY, FINALS_GAME_INTERVAL, SimpleNamespace(random=lambda: 0))
            match["games"][-1]["message_id"] = 101
        match["dirty"] = False
        event["board_dirty"] = False
        event.update(prizes_paid=True, prizes_message_id=888)
        state = {"config": CONFIG.copy(), "event": event}
        channel = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("Discord is unavailable")))
        self.bot.get_channel = lambda channel_id: channel
        with self.assertRaises(RuntimeError):
            await Tournaments.publish_daily_recaps(self.cog, event)
        self.assertNotIn("2026-10-25", event.get("daily_recaps", {}))
        self.assertFalse(self.cog.ensure_week(state, MONDAY + timedelta(days=7)))
        channel.send.side_effect = None
        channel.send.return_value = SimpleNamespace(id=556)
        await Tournaments.publish_daily_recaps(self.cog, event)
        self.assertTrue(self.cog.ensure_week(state, MONDAY + timedelta(days=7)))

    async def test_prize_payout_survives_crash_between_balance_write_and_event_flag(self):
        event = event_with(4)
        engine.lock_registration(event, leaderboard_order=list(event["entrants"]))
        sunday = datetime(2026, 10, 26, tzinfo=timezone.utc)
        while match := engine.next_match(event, sunday):
            engine.play_game(event, match, sunday, FINALS_GAME_INTERVAL, SimpleNamespace(random=lambda: 0))
            match["games"][-1]["message_id"] = 101
        users = Users.__new__(Users)
        users.users = {uid: {"gold": 2, "cash": 100, "elo": 1200, "xp": 50} for uid in event["entrants"]}
        self.cogs["Users"] = users
        self.cog.data = {"guilds": {"123": {"config": CONFIG.copy(), "event": event}}}
        channel = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("Discord unavailable")))
        self.bot.get_channel = lambda channel_id: channel
        with self.assertRaises(RuntimeError):
            await Tournaments.finish_tournament(self.cog, event)
        self.assertTrue(event["prizes_paid"])
        self.assertNotIn("prizes_message_id", event)
        balances = copy.deepcopy(users.users)
        for prize in engine.tournament_prizes(event):
            profile = balances[prize["user_id"]]
            self.assertEqual(profile["gold"], 2 + prize["gold"])
            self.assertEqual(profile["cash"], 100 + prize["cash"])
            self.assertEqual((profile["elo"], profile["xp"]), (1200, 50))
        # Simulate the process dying after users.json commits but before tournaments.json marks payment.
        event["prizes_paid"] = False
        self.cog.save()
        users.users = json.loads((Path(self.temp.name) / "users.json").read_text())
        restarted = Tournaments(self.bot)
        channel.send.side_effect = None
        channel.send.return_value = SimpleNamespace(id=999)
        restored_event = restarted.guild_state(123)["event"]
        await restarted.finish_tournament(restored_event)
        self.assertEqual(users.users, balances)
        self.assertEqual(restored_event["prizes_message_id"], 999)
        await restarted.finish_tournament(restored_event)
        self.assertEqual(channel.send.await_count, 2)  # Failed send, then one successful announcement.

    async def test_failed_prize_save_rolls_back_balances_and_receipts(self):
        users = Users.__new__(Users)
        users.users = {"1": {"gold": 2, "cash": 100}, "2": {"gold": 3, "cash": 200}}
        original = copy.deepcopy(users.users)
        retained_profile = users.users["1"]
        prizes = [{"user_id": "1", "place": 1, "gold": 20, "cash": 500},
                  {"user_id": "2", "place": 2, "gold": 10, "cash": 250}]
        with patch.object(users, "save_users", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                users.award_tournament_prizes("practice-prize", prizes)
        self.assertEqual(users.users, original)
        self.assertIs(users.users["1"], retained_profile)
        users.award_tournament_prizes("practice-prize", prizes)
        users.award_tournament_prizes("practice-prize", prizes)
        self.assertEqual(users.users["1"]["gold"], 22)
        self.assertEqual(users.users["1"]["cash"], 600)
        self.assertEqual(users.users["2"]["gold"], 13)
        self.assertEqual(users.users["2"]["cash"], 450)


class DailyBracketTests(unittest.TestCase):
    def test_group_stage_bracket_is_available_before_qualifiers_are_known(self):
        for count in (12, 32):
            event = event_with(count)
            engine.lock_registration(event, leaderboard_order=list(event["entrants"]))
            original = copy.deepcopy(event)
            with Image.open(render_bracket(event, "2026-10-22", title="Tournament bracket")) as image:
                self.assertEqual(image.format, "PNG")
                self.assertGreater(image.height, 800)
            self.assertEqual(event, original)

    def test_prizes_reward_only_finalists_and_real_semifinal_losers(self):
        for count in (2, 3, 4, 6, 12):
            event = event_with(count)
            engine.lock_registration(event, leaderboard_order=list(event["entrants"]))
            self.assertEqual(engine.tournament_prizes(event), [])
            now = datetime(2026, 10, 26, tzinfo=timezone.utc)
            while match := engine.next_match(event, now):
                engine.play_game(event, match, now, GAME_INTERVAL, SimpleNamespace(random=lambda: 0))
            prizes = engine.tournament_prizes(event)
            self.assertEqual(len(prizes), min(count, 4))
            self.assertEqual(len({prize["user_id"] for prize in prizes}), len(prizes))
            self.assertEqual(prizes[0], {"user_id": event["winner"], "place": 1, "gold": 20, "cash": 500})
            self.assertEqual((prizes[1]["gold"], prizes[1]["cash"]), (10, 250))
            for prize in prizes[2:]:
                self.assertEqual((prize["gold"], prize["cash"]), (5, 150))
            event["state"] = "cancelled"
            self.assertEqual(engine.tournament_prizes(event), [])

    def test_match_time_estimates_account_for_pacing_byes_and_same_day_rounds(self):
        for count in (4, 8, 9):
            event = event_with(count)
            engine.lock_registration(event, leaderboard_order=list(event["entrants"]))
            estimates = estimated_match_times(event)
            if count == 4:
                start = datetime.fromisoformat(event["stage_times"]["semifinal"])
                self.assertEqual(estimates["semifinal", 0], start)
                self.assertEqual(estimates["semifinal", 1], start + timedelta(hours=2, seconds=30))
            elif count == 8:
                start = datetime.fromisoformat(event["stage_times"]["quarterfinal"])
                self.assertEqual(estimates["quarterfinal", 3], start + timedelta(minutes=9))
                self.assertEqual(estimates["semifinal", 0], datetime.fromisoformat(event["stage_times"]["semifinal"]))
            else:
                start = datetime.fromisoformat(event["stage_times"]["preliminary"])
                self.assertEqual(sum(value is None for (stage, _), value in estimates.items() if stage == "preliminary"), 7)
                self.assertEqual(estimates["quarterfinal", 0], start + timedelta(minutes=3))

    def test_daily_completion_waits_for_all_rounds_and_published_games(self):
        for count, expected_days in ((2, ["2026-10-25"]), (4, ["2026-10-24", "2026-10-25"]),
                                     (8, ["2026-10-23", "2026-10-24", "2026-10-25"]),
                                     (9, ["2026-10-23", "2026-10-24", "2026-10-25"]),
                                     (16, ["2026-10-22", "2026-10-23", "2026-10-24", "2026-10-25"])):
            with self.subTest(entrants=count):
                event = event_with(count)
                engine.lock_registration(event, random.Random(0))
                now = datetime(2026, 10, 26, tzinfo=timezone.utc)
                reported = []
                while match := engine.next_match(event, now):
                    engine.play_game(event, match, now, GAME_INTERVAL, random.Random(match["id"]))
                    # Even the last result must be published before the picture can go out.
                    self.assertEqual(engine.pending_daily_recaps(event), [])
                    match["games"][-1]["message_id"] = 100
                    for day in engine.pending_daily_recaps(event):
                        reported.append(day)
                        with Image.open(render_bracket(event, day)) as image:
                            self.assertEqual(image.format, "PNG")
                            self.assertGreaterEqual(image.width, 900)
                        event.setdefault("daily_recaps", {})[day] = 200
                self.assertEqual(reported, expected_days)
                self.assertEqual(engine.pending_daily_recaps(event), [])

    def test_brackets_render_all_field_sizes_with_long_unicode_names(self):
        for count in range(2, 33):
            event = event_with(count)
            for uid, participant in event["entrants"].items():
                participant["name"] = f"{uid} · 玩家 🎮 " + "Very long display name " * 3
            engine.lock_registration(event, random.Random(0))
            now = datetime(2026, 10, 26, tzinfo=timezone.utc)
            while match := engine.next_match(event, now):
                engine.play_game(event, match, now, GAME_INTERVAL, random.Random(match["id"]))
            with Image.open(render_bracket(event, "2026-10-25")) as image:
                self.assertLess(image.width, 2000)
                self.assertLess(image.height, 2000)


if __name__ == "__main__":
    unittest.main()
