import asyncio
import os
import re
import uuid
from datetime import datetime, timedelta, timezone

import discord
from discord.ext import commands, tasks
import requests

from .storage import load_json, save_json


DATA_PATH = "data/predictions.json"
USERS_PATH = "data/users.json"
LOLESPORTS_BASE_URL = "https://esports-api.lolesports.com/persisted/gw"
LOLESPORTS_PUBLIC_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LOLESPORTS_LOCALE = "en-US"
MATCH_CACHE_TTL = timedelta(minutes=10)
MATCH_AUTO_REFRESH_MINUTES = 15
PREDICTION_RETRACT_LOCK = timedelta(minutes=30)
PREDICTION_PAGE_LIMIT = 4
PREDICTION_MAX_STAKE = 50000
PREDICTION_MIN_STAKE = 1
PREDICTION_SOURCE_URL = "https://lolesports.com/schedule"
GAME_LEAGUE = "League of Legends"
SORT_START = "start"
SORT_POOL = "pool"


class PredictionBidModal(discord.ui.Modal, title="Make Prediction"):
    team = discord.ui.TextInput(
        label="Team",
        placeholder="Use 1, 2, or the team name",
        max_length=80,
    )
    amount = discord.ui.TextInput(
        label="Cash Amount",
        placeholder="Example: 50",
        max_length=8,
    )

    def __init__(self, view, match_id):
        super().__init__()
        self.view_ref = view
        self.match_id = match_id

    async def on_submit(self, interaction: discord.Interaction):
        match = self.view_ref.cog.data.get("matches", {}).get(self.match_id)
        if not match:
            await interaction.response.send_message("That match is no longer available.", ephemeral=True)
            return

        try:
            amount = int(str(self.amount.value).strip())
        except ValueError:
            await interaction.response.send_message("Cash amount must be a whole number.", ephemeral=True)
            return

        team = self.view_ref.cog.resolve_team_choice(match, str(self.team.value).strip())
        if not team:
            await interaction.response.send_message(
                f"Pick `{match['team1']['name']}` or `{match['team2']['name']}`.",
                ephemeral=True,
            )
            return

        error = self.view_ref.cog.place_prediction(interaction.user.id, match, team, amount)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        await interaction.response.send_message(
            f"Prediction locked: **{team['name']}** for **{amount} cash**.",
            ephemeral=True,
        )
        await self.view_ref.refresh_message()


class PredictionMatchSelect(discord.ui.Select):
    def __init__(self, view):
        self.prediction_view = view
        options = []
        for match in view.matches[:25]:
            pool = view.cog.total_pool(match["id"])
            options.append(discord.SelectOption(
                label=view.cog.match_label(match)[:100],
                description=f"{match.get('league', 'LoL')} - {view.cog.format_match_time(match)} - Pool {pool}"[:100],
                value=match["id"],
            ))

        super().__init__(
            placeholder="Choose a match to predict...",
            min_values=1,
            max_values=1,
            options=options,
            row=1,
        )

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(PredictionBidModal(self.prediction_view, self.values[0]))


class PredictionView(discord.ui.View):
    def __init__(self, cog, author_id, sort_mode=SORT_START):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id
        self.sort_mode = sort_mode
        self.matches = []
        self.message = None
        self.rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Use your own `.prediction` menu.", ephemeral=True)
            return False
        return True

    def rebuild_items(self):
        self.clear_items()
        self.matches = self.cog.sorted_prediction_matches(self.sort_mode)
        self.nearest_button.style = discord.ButtonStyle.primary if self.sort_mode == SORT_START else discord.ButtonStyle.secondary
        self.pool_button.style = discord.ButtonStyle.primary if self.sort_mode == SORT_POOL else discord.ButtonStyle.secondary
        self.add_item(self.nearest_button)
        self.add_item(self.pool_button)
        self.add_item(self.mine_button)
        if self.matches:
            self.add_item(PredictionMatchSelect(self))

    def build_embed(self):
        return self.cog.build_prediction_embed(self.matches, self.sort_mode)

    async def refresh_message(self):
        self.rebuild_items()
        if self.message:
            await self.message.edit(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Nearest Start", style=discord.ButtonStyle.primary, row=0)
    async def nearest_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sort_mode = SORT_START
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Biggest Pool", style=discord.ButtonStyle.secondary, row=0)
    async def pool_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.sort_mode = SORT_POOL
        self.rebuild_items()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="My Predictions", style=discord.ButtonStyle.secondary, row=0)
    async def mine_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = MyPredictionsView(self.cog, interaction.user.id, interaction.user.display_name)
        await interaction.response.send_message(embed=view.build_embed(), view=view, ephemeral=True)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True
        if self.message:
            await self.message.edit(view=self)


class RetractPredictionSelect(discord.ui.Select):
    def __init__(self, view):
        self.predictions_view = view
        options = []
        for prediction in view.retractable_predictions[:25]:
            match = view.cog.prediction_match(prediction)
            options.append(discord.SelectOption(
                label=(view.cog.match_label(match) if match else prediction.get("team_name", "Prediction"))[:100],
                description=f"{prediction.get('team_name')} - {prediction.get('amount')} cash"[:100],
                value=prediction["prediction_id"],
            ))

        super().__init__(
            placeholder="Retract an eligible prediction...",
            min_values=1,
            max_values=1,
            options=options,
            row=0,
        )

    async def callback(self, interaction: discord.Interaction):
        prediction_id = self.values[0]
        prediction = self.predictions_view.cog.get_prediction(prediction_id)
        if not prediction:
            await interaction.response.send_message("That prediction is no longer available.", ephemeral=True)
            return

        prediction, error = self.predictions_view.cog.retract_prediction_by_id(interaction.user.id, prediction_id)
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        self.predictions_view.rebuild_items()
        await interaction.response.edit_message(embed=self.predictions_view.build_embed(), view=self.predictions_view)


class MyPredictionsView(discord.ui.View):
    def __init__(self, cog, author_id, display_name):
        super().__init__(timeout=180)
        self.cog = cog
        self.author_id = author_id
        self.display_name = display_name
        self.retractable_predictions = []
        self.rebuild_items()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("You can only manage your own predictions.", ephemeral=True)
            return False
        return True

    def rebuild_items(self):
        self.clear_items()
        self.retractable_predictions = self.cog.retractable_user_predictions(self.author_id)
        if self.retractable_predictions:
            self.add_item(RetractPredictionSelect(self))

    def build_embed(self):
        return self.cog.build_user_predictions_embed(self.author_id, self.display_name)

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


class Predictions(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = self.load_predictions()

    async def cog_load(self):
        self.auto_refresh_matches.start()

    def cog_unload(self):
        self.auto_refresh_matches.cancel()

    @tasks.loop(minutes=MATCH_AUTO_REFRESH_MINUTES)
    async def auto_refresh_matches(self):
        try:
            await asyncio.to_thread(self.refresh_matches_sync)
        except Exception as exc:
            print(f"Prediction auto-refresh failed: {exc}")

    @auto_refresh_matches.before_loop
    async def before_auto_refresh_matches(self):
        await self.bot.wait_until_ready()

    def load_predictions(self):
        data = load_json(DATA_PATH, default={})
        if not isinstance(data, dict):
            data = {}
        data.setdefault("matches", {})
        data.setdefault("predictions", [])
        data.setdefault("last_refresh", None)
        return data

    def save_predictions(self):
        save_json(DATA_PATH, self.data)

    def load_users(self):
        users_cog = self.bot.get_cog("Users") if self.bot else None
        if users_cog is not None:
            return users_cog.users
        return load_json(USERS_PATH, default={})

    def save_users(self, users):
        users_cog = self.bot.get_cog("Users") if self.bot else None
        if users_cog is not None:
            users_cog.users = users
            users_cog.save_users()
            return
        save_json(USERS_PATH, users)

    def utc_now(self):
        return datetime.now(timezone.utc)

    def parse_time(self, value):
        if not value:
            return None
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def normalize_key(self, value):
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    def team_display(self, team):
        return team.get("name") or team.get("code") or "TBD"

    def known_lol_team_keys(self):
        cards_cog = self.bot.get_cog("Cards") if self.bot else None
        if cards_cog is None:
            return {}

        teams = {}
        for card in cards_cog.cards.values():
            if cards_cog.card_game(card) != GAME_LEAGUE:
                continue
            team_name = card.get("team")
            if not team_name:
                continue
            teams[self.normalize_key(team_name)] = team_name
        return teams

    def event_team(self, team, known_teams):
        name = self.team_display(team)
        code = team.get("code") or ""
        name_key = self.normalize_key(name)
        code_key = self.normalize_key(code)
        known_name = known_teams.get(name_key) or known_teams.get(code_key)
        if not known_name:
            return None

        return {
            "name": known_name,
            "source_name": name,
            "code": code,
            "key": self.normalize_key(known_name),
            "result": team.get("result", {}),
        }

    def event_to_match(self, event, known_teams):
        if not isinstance(event, dict) or event.get("type") != "match":
            return None

        match = event.get("match") or {}
        source_teams = match.get("teams") or []
        if len(source_teams) < 2:
            return None

        team1 = self.event_team(source_teams[0], known_teams)
        team2 = self.event_team(source_teams[1], known_teams)
        if not team1 or not team2:
            return None

        winner = None
        for team in (team1, team2):
            outcome = str(team.get("result", {}).get("outcome", "")).lower()
            if outcome == "win":
                winner = {"key": team["key"], "name": team["name"]}
                break

        league = event.get("league") or {}
        strategy = match.get("strategy") or {}
        return {
            "id": str(event.get("id") or match.get("id")),
            "match_id": str(match.get("id") or event.get("id")),
            "league": league.get("name", "LoL Esports"),
            "league_slug": league.get("slug"),
            "block_name": event.get("blockName"),
            "start_time": event.get("startTime"),
            "state": event.get("state", "unstarted"),
            "team1": team1,
            "team2": team2,
            "best_of": strategy.get("count"),
            "winner": winner,
            "source": "LoL Esports",
            "source_url": PREDICTION_SOURCE_URL,
            "last_fetched_at": self.utc_now().isoformat(),
        }

    def api_headers(self):
        return {
            "x-api-key": os.getenv("LOLESPORTS_API_KEY", LOLESPORTS_PUBLIC_KEY),
            "User-Agent": "ProPulse Discord Bot predictions/1.0",
        }

    def fetch_schedule_page(self, page_token=None):
        params = {"hl": LOLESPORTS_LOCALE}
        if page_token:
            params["pageToken"] = page_token
        response = requests.get(
            f"{LOLESPORTS_BASE_URL}/getSchedule",
            params=params,
            headers=self.api_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("data", {}).get("schedule", {})

    def fetch_event_details(self, match):
        match_id = match.get("match_id") or match.get("id")
        if not match_id:
            return None
        response = requests.get(
            f"{LOLESPORTS_BASE_URL}/getEventDetails",
            params={"hl": LOLESPORTS_LOCALE, "id": match_id},
            headers=self.api_headers(),
            timeout=15,
        )
        response.raise_for_status()
        return response.json().get("data", {}).get("event")

    def refresh_matches_sync(self):
        known_teams = self.known_lol_team_keys()
        if not known_teams:
            return 0, "No League of Legends card teams are loaded."

        pages = []
        current = self.fetch_schedule_page()
        pages.append(current)

        page_token = (current.get("pages") or {}).get("newer")
        for _ in range(PREDICTION_PAGE_LIMIT - 1):
            if not page_token:
                break
            page = self.fetch_schedule_page(page_token)
            pages.append(page)
            page_token = (page.get("pages") or {}).get("newer")

        found = 0
        matches = self.data.setdefault("matches", {})
        for page in pages:
            for event in page.get("events", []):
                match = self.event_to_match(event, known_teams)
                if not match:
                    continue
                found += 1
                existing = matches.get(match["id"], {})
                existing.update(match)
                matches[match["id"]] = existing

        self.data["last_refresh"] = self.utc_now().isoformat()
        resolved = self.settle_completed_predictions()
        self.save_predictions()

        message = f"Found {found} card-team LoL matches."
        if resolved:
            message += f" Resolved {resolved} predictions."
        return found, message

    async def refresh_matches(self, force=False):
        if not force and not self.cache_is_stale():
            return 0, None
        return await asyncio.to_thread(self.refresh_matches_sync)

    def cache_is_stale(self):
        last_refresh = self.parse_time(self.data.get("last_refresh"))
        if not last_refresh:
            return True
        return self.utc_now() - last_refresh > MATCH_CACHE_TTL

    def sorted_matches(self, include_completed=False):
        now = self.utc_now()
        matches = []
        for match in self.data.get("matches", {}).values():
            start_time = self.parse_time(match.get("start_time"))
            if not start_time:
                continue
            state = match.get("state")
            if not include_completed:
                if state == "completed" or start_time <= now:
                    continue
            matches.append(match)
        matches.sort(key=lambda match: self.parse_time(match.get("start_time")) or now)
        return matches

    def sorted_prediction_matches(self, sort_mode=SORT_START):
        matches = self.sorted_matches()
        if sort_mode == SORT_POOL:
            matches.sort(key=lambda match: (-self.total_pool(match["id"]), self.parse_time(match.get("start_time")) or self.utc_now()))
        return matches

    def format_match_time(self, match):
        start_time = self.parse_time(match.get("start_time"))
        if not start_time:
            return "Time TBD"
        return start_time.strftime("%b %d %H:%M UTC")

    def match_label(self, match):
        team1 = match["team1"]["name"]
        team2 = match["team2"]["name"]
        best_of = f" Bo{match['best_of']}" if match.get("best_of") else ""
        return f"{team1} vs {team2}{best_of}"

    def build_matches_embed(self, matches):
        embed = discord.Embed(
            title="LoL Predictions",
            color=discord.Color.dark_grey(),
        )
        if not matches:
            embed.description = "No upcoming card-team LoL predictions are available right now."
            return embed

        lines = []
        for index, match in enumerate(matches[:20], start=1):
            pool = self.match_pool(match["id"])
            lines.append(
                f"`{index}` **{self.match_label(match)}**\n"
                f"{match.get('league', 'LoL Esports')} - {self.format_match_time(match)}\n"
                f"Pool: {match['team1']['name']} {pool.get(match['team1']['key'], 0)} - "
                f"{match['team2']['name']} {pool.get(match['team2']['key'], 0)}"
            )
        embed.description = "\n\n".join(lines)
        embed.set_footer(text="Use .prediction to select a match. Fictional ProPulse cash only.")
        return embed

    def build_prediction_embed(self, matches, sort_mode=SORT_START):
        sort_label = "Biggest prize pool" if sort_mode == SORT_POOL else "Nearest start time"
        embed = discord.Embed(
            title="LoL Predictions",
            description=f"Sorted by **{sort_label}**.",
            color=discord.Color.dark_grey(),
        )
        if not matches:
            embed.description = "No upcoming card-team LoL predictions are available right now."
            return embed

        for index, match in enumerate(matches[:10], start=1):
            pool = self.match_pool(match["id"])
            team1 = match["team1"]
            team2 = match["team2"]
            total = self.total_pool(match["id"])
            embed.add_field(
                name=f"{index}. {self.match_label(match)}",
                value=(
                    f"{match.get('league', 'LoL Esports')} - {self.format_match_time(match)}\n"
                    f"Prize pool: **{total} cash**\n"
                    f"{team1['name']}: {pool.get(team1['key'], 0)} | "
                    f"{team2['name']}: {pool.get(team2['key'], 0)}"
                ),
                inline=False,
            )

        embed.set_footer(text="Select a match to predict. Predictions use fictional ProPulse cash only.")
        return embed

    def resolve_match_arg(self, value):
        text = str(value).strip()
        matches = self.sorted_matches()
        if text.isdigit():
            index = int(text)
            if 1 <= index <= len(matches):
                return matches[index - 1]

        matches_by_id = self.data.get("matches", {})
        if text in matches_by_id:
            return matches_by_id[text]

        for match in matches_by_id.values():
            if str(match.get("match_id")) == text:
                return match
            if str(match.get("id", "")).endswith(text):
                return match
        return None

    def resolve_team_choice(self, match, team_text):
        wanted = self.normalize_key(team_text)
        team1 = match["team1"]
        team2 = match["team2"]
        if wanted in {"1", "a", self.normalize_key(team1["name"]), self.normalize_key(team1.get("code"))}:
            return team1
        if wanted in {"2", "b", self.normalize_key(team2["name"]), self.normalize_key(team2.get("code"))}:
            return team2
        return None

    def user_prediction_for_match(self, user_id, match_id):
        uid = str(user_id)
        for prediction in self.data.get("predictions", []):
            if (
                prediction.get("user_id") == uid
                and prediction.get("match_id") == match_id
                and prediction.get("status") == "active"
            ):
                return prediction
        return None

    def active_predictions_for_match(self, match_id):
        return [
            prediction
            for prediction in self.data.get("predictions", [])
            if prediction.get("match_id") == match_id and prediction.get("status") == "active"
        ]

    def match_pool(self, match_id):
        pool = {}
        for prediction in self.active_predictions_for_match(match_id):
            team_key = prediction.get("team_key")
            pool[team_key] = pool.get(team_key, 0) + int(prediction.get("amount", 0))
        return pool

    def total_pool(self, match_id):
        return sum(self.match_pool(match_id).values())

    def place_prediction(self, user_id, match, team, amount):
        start_time = self.parse_time(match.get("start_time"))
        if not start_time or start_time <= self.utc_now():
            return "Predictions are locked for that match."
        if match.get("state") != "unstarted":
            return "Predictions are locked for that match."
        if amount < PREDICTION_MIN_STAKE:
            return f"Prediction amount must be at least {PREDICTION_MIN_STAKE} cash."
        if amount > PREDICTION_MAX_STAKE:
            return f"Prediction amount cannot exceed {PREDICTION_MAX_STAKE} cash."

        match_id = match["id"]
        if self.user_prediction_for_match(user_id, match_id):
            return "You already have an active prediction on that match."

        users = self.load_users()
        users_cog = self.bot.get_cog("Users") if self.bot else None
        if users_cog is not None:
            profile = users_cog.get_profile_by_id(user_id)
            users = users_cog.users
        else:
            profile = users.setdefault(str(user_id), {})

        cash = int(profile.get("cash", 0))
        if cash < amount:
            return f"You only have {cash} cash."

        profile["cash"] = cash - amount
        self.save_users(users)

        self.data.setdefault("predictions", []).append({
            "prediction_id": str(uuid.uuid4()),
            "user_id": str(user_id),
            "match_id": match_id,
            "source_match_id": match.get("match_id"),
            "team_key": team["key"],
            "team_name": team["name"],
            "amount": amount,
            "status": "active",
            "placed_at": self.utc_now().isoformat(),
            "resolved_at": None,
            "payout": 0,
        })
        self.save_predictions()
        return None

    def retract_prediction(self, user_id, match_ref):
        match = self.resolve_match_arg(match_ref)
        if not match:
            return None, "I couldn't find that match. Use `.prediction` to see current predictions."

        start_time = self.parse_time(match.get("start_time"))
        if not start_time:
            return None, "That match does not have a start time yet."

        lock_time = start_time - PREDICTION_RETRACT_LOCK
        if self.utc_now() >= lock_time:
            return None, "Predictions can only be retracted until 30 minutes before match start."

        prediction = self.user_prediction_for_match(user_id, match["id"])
        if not prediction:
            return None, "You do not have an active prediction on that match."

        users = self.load_users()
        users_cog = self.bot.get_cog("Users") if self.bot else None
        if users_cog is not None:
            profile = users_cog.get_profile_by_id(user_id)
            users = users_cog.users
        else:
            profile = users.setdefault(str(user_id), {})

        amount = int(prediction.get("amount", 0))
        profile["cash"] = int(profile.get("cash", 0)) + amount
        self.save_users(users)

        prediction["status"] = "retracted"
        prediction["resolved_at"] = self.utc_now().isoformat()
        prediction["payout"] = amount
        prediction["refund_reason"] = "user_retracted"
        self.save_predictions()

        return prediction, None

    def get_prediction(self, prediction_id):
        for prediction in self.data.get("predictions", []):
            if prediction.get("prediction_id") == prediction_id:
                return prediction
        return None

    def retract_prediction_by_id(self, user_id, prediction_id):
        prediction = self.get_prediction(prediction_id)
        if not prediction:
            return None, "That prediction is no longer available."
        if prediction.get("user_id") != str(user_id):
            return None, "You can only retract your own predictions."
        if prediction.get("status") != "active":
            return None, "That prediction is no longer active."

        match = self.data.get("matches", {}).get(prediction.get("match_id"))
        if not match:
            return None, "That match is no longer available."

        start_time = self.parse_time(match.get("start_time"))
        if not start_time:
            return None, "That match does not have a start time yet."
        if self.utc_now() >= start_time - PREDICTION_RETRACT_LOCK:
            return None, "Predictions can only be retracted until 30 minutes before match start."

        users = self.load_users()
        users_cog = self.bot.get_cog("Users") if self.bot else None
        if users_cog is not None:
            profile = users_cog.get_profile_by_id(user_id)
            users = users_cog.users
        else:
            profile = users.setdefault(str(user_id), {})

        amount = int(prediction.get("amount", 0))
        profile["cash"] = int(profile.get("cash", 0)) + amount
        self.save_users(users)

        prediction["status"] = "retracted"
        prediction["resolved_at"] = self.utc_now().isoformat()
        prediction["payout"] = amount
        prediction["refund_reason"] = "user_retracted"
        self.save_predictions()
        return prediction, None

    def retractable_user_predictions(self, user_id):
        uid = str(user_id)
        retractable = []
        for prediction in self.data.get("predictions", []):
            if prediction.get("user_id") != uid or prediction.get("status") != "active":
                continue
            match = self.prediction_match(prediction)
            start_time = self.parse_time(match.get("start_time"))
            if start_time and self.utc_now() < start_time - PREDICTION_RETRACT_LOCK:
                retractable.append(prediction)
        retractable.sort(key=lambda prediction: self.parse_time(self.prediction_match(prediction).get("start_time")) or self.utc_now())
        return retractable

    def update_match_from_details(self, match):
        event = self.fetch_event_details(match)
        if not event:
            return match
        known_teams = self.known_lol_team_keys()
        updated = self.event_to_match(event, known_teams)
        if updated:
            match.update(updated)
            self.data.setdefault("matches", {})[match["id"]] = match
        return match

    def pooled_winner_payouts(self, winners, losing_pool):
        winning_pool = sum(int(prediction.get("amount", 0)) for prediction in winners)
        if winning_pool <= 0:
            return {}

        payouts = {}
        remainders = []
        used_losing_pool = 0
        for prediction in winners:
            amount = int(prediction.get("amount", 0))
            numerator = losing_pool * amount
            share = numerator // winning_pool
            used_losing_pool += share
            payouts[prediction["prediction_id"]] = amount + share
            remainders.append((numerator % winning_pool, prediction["prediction_id"]))

        leftover = losing_pool - used_losing_pool
        remainders.sort(reverse=True)
        for _, prediction_id in remainders[:leftover]:
            payouts[prediction_id] += 1
        return payouts

    def refund_match_predictions(self, predictions, users, reason):
        for prediction in predictions:
            uid = prediction["user_id"]
            profile = users.setdefault(uid, {})
            amount = int(prediction.get("amount", 0))
            profile["cash"] = int(profile.get("cash", 0)) + amount
            prediction["status"] = "refunded"
            prediction["refund_reason"] = reason
            prediction["resolved_at"] = self.utc_now().isoformat()
            prediction["payout"] = amount

    def settle_completed_predictions(self):
        users = self.load_users()
        changed_users = False
        resolved = 0

        active_by_match = {}
        for prediction in self.data.get("predictions", []):
            if prediction.get("status") == "active":
                active_by_match.setdefault(prediction.get("match_id"), []).append(prediction)

        for match_id, predictions in active_by_match.items():
            match = self.data.get("matches", {}).get(match_id)
            if not match:
                continue

            if match.get("state") != "completed" or not match.get("winner"):
                try:
                    match = self.update_match_from_details(match)
                except requests.RequestException:
                    continue

            winner = match.get("winner")
            if match.get("state") != "completed" or not winner:
                continue

            pools = {}
            for prediction in predictions:
                team_key = prediction.get("team_key")
                pools[team_key] = pools.get(team_key, 0) + int(prediction.get("amount", 0))

            winner_key = winner.get("key")
            winning_pool = pools.get(winner_key, 0)
            losing_pool = sum(amount for team_key, amount in pools.items() if team_key != winner_key)
            if len([amount for amount in pools.values() if amount > 0]) < 2 or winning_pool <= 0 or losing_pool <= 0:
                self.refund_match_predictions(predictions, users, "no_opposing_predictions")
                resolved += len(predictions)
                changed_users = True
                continue

            winners = [prediction for prediction in predictions if prediction.get("team_key") == winner_key]
            payouts = self.pooled_winner_payouts(winners, losing_pool)

            for prediction in predictions:
                uid = prediction["user_id"]
                profile = users.setdefault(uid, {})
                won = prediction.get("team_key") == winner_key
                payout = payouts.get(prediction["prediction_id"], 0) if won else 0
                if payout:
                    profile["cash"] = int(profile.get("cash", 0)) + payout
                    changed_users = True

                prediction["status"] = "won" if won else "lost"
                prediction["winner_name"] = winner.get("name")
                prediction["resolved_at"] = self.utc_now().isoformat()
                prediction["payout"] = payout
                prediction["winning_pool"] = winning_pool
                prediction["losing_pool"] = losing_pool
                resolved += 1

        if changed_users:
            self.save_users(users)
        return resolved

    def prediction_match(self, prediction):
        return self.data.get("matches", {}).get(prediction.get("match_id"), {})

    def build_user_predictions_embed(self, user_id, display_name):
        uid = str(user_id)
        predictions = [
            prediction
            for prediction in self.data.get("predictions", [])
            if prediction.get("user_id") == uid
        ]
        predictions.sort(key=lambda p: p.get("placed_at", ""), reverse=True)
        embed = discord.Embed(title=f"{display_name}'s Predictions", color=discord.Color.dark_grey())
        if not predictions:
            embed.description = "You have not placed any predictions yet."
            return embed

        lines = []
        for prediction in predictions[:15]:
            match = self.prediction_match(prediction)
            status = prediction.get("status", "active").capitalize()
            payout = prediction.get("payout", 0)
            payout_text = f" - Payout {payout}" if payout else ""
            if prediction.get("status") == "refunded":
                payout_text = f" - Refunded {payout}"
            if prediction.get("status") == "retracted":
                payout_text = f" - Retracted {payout}"
            lines.append(
                f"**{self.match_label(match) if match else prediction.get('source_match_id', 'Unknown match')}**\n"
                f"{status}: {prediction.get('team_name')} for {prediction.get('amount')} cash{payout_text}"
            )
        embed.description = "\n\n".join(lines)
        return embed

    @commands.command(aliases=["predictions", "pred", "preds"])
    async def prediction(self, ctx):
        async with ctx.typing():
            _, message = await self.refresh_matches(force=False)
        if message and message.startswith("No League of Legends"):
            await ctx.send(message)
            return

        view = PredictionView(self, ctx.author.id)
        message = await ctx.send(embed=view.build_embed(), view=view)
        view.message = message


async def setup(bot):
    await bot.add_cog(Predictions(bot))
