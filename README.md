# ValoPro
discord bot

## Weekly tournaments

Moderators can enable a weekly tournament in a Discord text channel:

```text
.tournament setup #tournament lol America/New_York 17:00
```

Use `valorant` instead of `lol` for a Valorant event. Each server has one weekly tournament,
for the selected game. Setup verifies the bot can view the channel, send messages, embed links,
attach files, and read message history. Scheduling starts only after setup; deploying the code
does not start tournaments or send announcements by itself.
Create the `#tournament` text channel first. Running `.tournament setup` without arguments
finds that channel in the current server and uses the defaults above. The configured channel
receives the signup board, match announcements, every game result, and bracket pictures.

The default schedule uses Eastern local time (EST/EDT), including daylight saving changes:

| Day | Event |
| --- | --- |
| Monday–Wednesday | Signups; each player must sign up again every week |
| Thursday 00:00 | Registration closes, equipped lineups are validated and locked |
| Thursday 17:00 | Group stage, only for 12 or more teams |
| Friday 17:00 | Preliminary knockout matches if needed, then quarterfinals |
| Saturday 17:00 | Semifinals |
| Sunday 17:00 | Final |

Stages begin at the configured time, and their series run one after another in the broadcast
channel. Small fields skip rounds they do not need: 2 teams play the Sunday final; 3–4 start
Saturday; 5–8 start Friday. When a knockout bracket needs byes, they go to the highest-ranked
registered players on `.leaderboard` at signup close. If several byes are needed, they go to
the top eligible entrants in order. Leaderboard ordering includes its normal tiebreakers
(ELO, total power, name, then user ID). Rankings and bye recipients are frozen for that week;
every entrant receives a tournament seed from #1 through the number of entrants, based on
that leaderboard order. Seeds are relative to this week's entrants, rather than the player's
global leaderboard position. Knockout brackets use standard seeding (for example, #1 vs #8
and #2 vs #7 in an eight-player field). Groups use snake seeding to spread higher seeds
across the four groups and do not award byes. Tournament seeds appear on `.leaderboard`,
match titles, scores and winner announcements, lineup artwork, and bracket pictures.
For 9–11 teams, a preliminary bracket with byes
reduces the field to eight on Friday, followed by the quarterfinals. No matches are broadcast
Thursday when there are fewer than 12 entrants. For 12–32 teams, entrants are seeded into four
balanced round-robin groups. Every team plays each other team in its group once, and the top
two per group advance. Quarterfinals cross group winners with runners-up; group opponents
cannot meet again until the final. Group ties use series wins, game differential, game wins,
then the tournament seed displayed on the standings. The initial capacity is 32 teams. Fewer than
two valid entrants cancels that week's event.

Every series, including groups, is **best of five**, ending as soon as a team wins three games.
All five roles use 100 base stats. Normal card rarity multipliers apply (Silver 1.25×, Gold 1.5×,
Diamond 1.75×, Master 2×, Challenger 2.25×); level stats and collection completion bonuses do
not apply. The existing ranked probability formula is used: `A² / (A² + B²)`, where A and B are
the two teams' total tournament power. Each game draws a fresh result. Tournament matches do
not change ranked ELO, ranked records, XP, or cards. Entry is free. After the final, prizes are
automatically added to the recipients' profiles and announced in the tournament channel:

| Placement | Gold | Cash |
| --- | --- | --- |
| 1st | 20 | 500 |
| 2nd | 10 | 250 |
| 3rd–4th, each semifinal loser | 5 | 150 |

No third-place playoff is required. Two-player events pay first and second only; three-player
events pay first, second, and their one semifinal loser. Cancelled events pay no prizes.
Balances and payout receipts are saved in the same atomic users-file write, preventing a
restart or failed Discord announcement from crediting the same tournament twice.

Players can use the persistent signup buttons or these commands:

```text
.tournament                 # Monday–Wednesday: sign up and show the seeded entrant list; Thursday–Sunday: bracket image
.tournament join            # Alias for the same signup flow
.tournament leave           # Withdraw before Wednesday ends
.tournament help            # Rules and commands
```

Keep a valid five-card lineup equipped until signup close. The team equipped at close is the
one entered, regardless of the player's default ranked game. Invalid teams at close are excluded
and listed on the board. After locking, the saved lineup stays unchanged for the whole event,
even if its owner changes their normal team. A new week starts with an empty signup list.
Using `.tournament` again while already entered confirms the existing signup and shows the
players again, sorted by provisional seed; it does not add a duplicate. Signup-list seeds
follow current leaderboard order until Wednesday ends, when the final seeds lock. After
Wednesday the command returns the bracket PNG instead of accepting signups. If groups have
not finished yet, Thursday shows a separate group standings image. Friday onward shows only
the knockout bracket. Events without groups always show the knockout bracket.
Viewing a board never plays a game or pays prizes.

The bot posts a series scoreboard and a **new message for every game**, including the winning
player, updated series score, and existing card artwork for both lineups. Each game post is
one broadcast image with the names and seeds once, power, three series-win markers per side,
and a highlighted winning lineup. A text result is used if artwork cannot be generated. The
main tournament board updates separately. Match titles, scores, winner announcements, and
artwork identify both entrants by their Discord display names, captured when lineups lock.
Posts do not ping players automatically. Group, preliminary, and quarterfinal games appear
30 seconds apart. Saturday semifinals and the Sunday final reveal game one at the series
start, then announce each subsequent game **30 minutes apart** (for example, 17:00, 17:30,
18:00, and, if needed, 18:30 and 19:00). Semifinal series run sequentially, with a short
30-second break between series. Game posts omit series IDs and repeated next-game reminders;
the artwork uses one scoreboard above both teams, with no repeated names or center caption.

After all matches scheduled for a match day finish, the bot automatically posts a PNG picture
of the updated standings or bracket in the same channel. Thursday uses a separate group
board with seeds, series records, game differences, and highlights for confirmed qualifiers.
Friday's picture waits for both preliminaries
and quarterfinals when both are needed. The picture includes entrants' display names, BO5
scores, byes, advancement connectors, and future-round placeholders, with no group tables
or bottom captions. Sunday's completed bracket highlights the winning final row. Days without matches do
not generate a picture. Estimated start times appear beneath each matchup, rather than
under the round headings. Estimates allow five games per series and skip byes; later
matches may start earlier when preceding series finish in fewer games. Posted recap message IDs are saved, preventing repeat pictures on
normal restarts; failed sends are retried, including after the final and before a new week opens.

Game announcements focus on the matchup artwork and score, without separate power/gold
comparison fields or a duplicate text lineup. The scoreboard shows each entrant's
seed, name, and power. Gold is rolled per role from that game's rarity-only power using the ranked gold calculation;
the lineup artwork also shows each role's gold. Each game's power, gold totals, and role rolls
are saved before the result is posted, so recovery reuses them. As in ranked, displayed gold
does not change the power-based win probability or count as wallet gold; tournament placement
prizes are credited separately after the final.

Moderator controls:

```text
.tournament channel #channel # Move the board and future broadcast messages
.tournament pause            # Stop scheduling, signups, and game reveals; preserve saved progress
.tournament resume           # Resume at the normal broadcast pace
.tournament cancel           # Cancel this week; next week's signup schedule remains enabled
```

Pausing does not extend signup deadlines. Setup accepts a different IANA timezone and 24-hour
match time. Cancel the active event before changing the weekly format; the new format takes
effect for the next event. If initially enabled Thursday–Sunday, signups begin the next Monday.

Tournament state is saved atomically in `data/tournaments.json` through the existing storage
layer, so it uses Railway's configured persistent volume. Scores, frozen lineups, signup state,
and Discord message IDs survive restarts. Overdue series resume one game at a time; they are
not rerolled or dumped into the channel at once. Missing channel permissions stop progress
until access is restored. If Discord accepts a send immediately before the process crashes,
the same saved result may be posted twice on recovery; its outcome is never rerolled.

Run the tournament checks locally without connecting a bot to Discord:

```text
python -m unittest discover -s tests -v
```

## Railway persistent data

The bot writes runtime state to JSON files such as `users.json`, `auctions.json`, `trades.json`, and `active_trades.json`. On Railway, files inside the app deploy directory are replaced on redeploy, so attach a Railway Volume to keep that state.

1. In Railway, open the bot service and add a Volume.
2. Mount it anywhere persistent, for example `/data`.
3. Redeploy the service.

The bot automatically uses `RAILWAY_VOLUME_MOUNT_PATH` when Railway provides it. You can also set `DATA_DIR` manually; if both are set, `DATA_DIR` wins.

On first startup with an empty volume, the checked-in files from `data/` are copied into the volume. After that, the volume copy is used and redeploys will not overwrite player progress.

## Ranked Discord roles

Ranked matches sync one role in the main Discord for each user: `Silver`, `Gold`, `Diamond`, `Champ`, or `Challenger`. The bot can create missing roles if it has `Manage Roles`; its bot role must be above those rank roles. Created roles use rank colors: silver, gold, purple, red, and blue.

Optional environment variables:

- `MAIN_DISCORD_GUILD_ID`: preferred server id for role sync.
- `MAIN_DISCORD_INVITE`: invite used to discover the server id, defaults to `https://discord.gg/fbJYSF2RfV`.
- `CHALLENGER_PULL_CHANNEL_ID`: channel id for Challenger card pull announcements.
- `CHALLENGER_PULL_CHANNEL_NAME`: channel name for Challenger card pull announcements, defaults to `challenger-pulls`.
- `CREATE_MISSING_CHALLENGER_PULL_CHANNEL`: set to `false` to require the announcement channel to already exist.
- `CREATE_MISSING_RANK_ROLES`: set to `false` to require roles to already exist.
- `RANK_ROLE_<RANK>_ID` or `RANK_ROLE_<RANK>_NAME`: override a role id/name, for example `RANK_ROLE_GOLD_ID`.

Admins can run `.syncrankroles` to backfill everyone after setup.

## Reaction roles

Admins can run `.reactionroles` in the channel where the notification panel should live, or `.reactionroles #channel-name` to post it elsewhere. The bot creates an `Update Pings` role if it does not already exist, posts a panel with a check reaction, and gives/removes that role when members add/remove the reaction.

The bot needs `Manage Roles`, `Send Messages`, and `Add Reactions`. Its bot role must be above the `Update Pings` role.

## Languages and Amazon Translate

User-facing bot strings are stored in `languages/en.json` and `languages/es.json`. Users can run `.language es` or `.lang es` to switch their profile to Spanish, and `.language en` to switch back.

To refresh the language files from source code and translate missing Spanish strings with Amazon Translate:

```powershell
python tools/update_languages.py --target es --amazon
```

To translate the existing English catalog into Spanish without re-extracting strings:

```powershell
python tools/amazon_translate_language.py --source en --target es --force
```

If AWS credentials are not configured, you can use the no-key Google Translate helper:

```powershell
python tools/google_translate_language.py --source en --target es --force
```

Amazon Translate uses the normal AWS credential chain. Set `AWS_REGION` or `AWS_DEFAULT_REGION` if you do not want the default `us-east-1`.
