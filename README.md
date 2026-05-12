# ValoPro
discord bot

## Railway persistent data

The bot writes runtime state to JSON files such as `users.json`, `auctions.json`, `trades.json`, and `active_trades.json`. On Railway, files inside the app deploy directory are replaced on redeploy, so attach a Railway Volume to keep that state.

1. In Railway, open the bot service and add a Volume.
2. Mount it anywhere persistent, for example `/data`.
3. Redeploy the service.

The bot automatically uses `RAILWAY_VOLUME_MOUNT_PATH` when Railway provides it. You can also set `DATA_DIR` manually; if both are set, `DATA_DIR` wins.

On first startup with an empty volume, the checked-in files from `data/` are copied into the volume. After that, the volume copy is used and redeploys will not overwrite player progress.
