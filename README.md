# ValoPro
discord bot

## Railway persistent data

The bot writes runtime state to JSON files such as `users.json`, `auctions.json`, `trades.json`, and `active_trades.json`. On Railway, files inside the app deploy directory are replaced on redeploy, so attach a Railway Volume to keep that state.

1. In Railway, open the bot service and add a Volume.
2. Mount it anywhere persistent, for example `/data`.
3. Redeploy the service.

The bot automatically uses `RAILWAY_VOLUME_MOUNT_PATH` when Railway provides it. You can also set `DATA_DIR` manually; if both are set, `DATA_DIR` wins.

On first startup with an empty volume, the checked-in files from `data/` are copied into the volume. After that, the volume copy is used and redeploys will not overwrite player progress.

## Ranked Discord roles

Ranked matches sync one role in the main Discord for each user: `Ranked Silver`, `Ranked Gold`, `Ranked Diamond`, `Ranked Champ`, or `Ranked Challenger`. The bot can create missing roles if it has `Manage Roles`; its bot role must be above those rank roles.

Optional environment variables:

- `MAIN_DISCORD_GUILD_ID`: preferred server id for role sync.
- `MAIN_DISCORD_INVITE`: invite used to discover the server id, defaults to `https://discord.gg/fbJYSF2RfV`.
- `CHALLENGER_PULL_CHANNEL_ID`: channel id for Challenger card pull announcements.
- `CHALLENGER_PULL_CHANNEL_NAME`: channel name for Challenger card pull announcements, defaults to `challenger-pulls`.
- `CREATE_MISSING_CHALLENGER_PULL_CHANNEL`: set to `false` to require the announcement channel to already exist.
- `CREATE_MISSING_RANK_ROLES`: set to `false` to require roles to already exist.
- `RANK_ROLE_<RANK>_ID` or `RANK_ROLE_<RANK>_NAME`: override a role id/name, for example `RANK_ROLE_GOLD_ID`.

Admins can run `.syncrankroles` to backfill everyone after setup.
