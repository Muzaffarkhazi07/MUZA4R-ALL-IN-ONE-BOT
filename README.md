# MUZAFFAR TASK REWARD BOT

## Files
- `bot.py` - Telegram bot
- `requirements.txt` - Python dependency
- `Procfile` - Railway worker start command

## Railway setup
1. Upload these files to a GitHub repository.
2. Create a Railway project from that repository.
3. Add Variables:
   - `BOT_TOKEN` = token from BotFather
   - `OWNER_ID` = `5839586443`
   - `DATABASE_PATH` = `/data/bot.sqlite3`
4. Add a Railway Volume and mount it at `/data` so SQLite data survives redeployments.
5. Deploy.

## BotFather
- Disable privacy mode if you need the bot to read ordinary group messages (not needed for the provided slash commands).
- Add the bot to groups and promote it to admin for reliable group tracking and group features.
- The bot does not need every admin permission. Grant only what is needed.

## Commands
Users:
- `/start`
- `/tasks`
- `/profile`
- `/leaderboard`
- `/help`
- `/cancel`

Owner:
- `/admin`
- `/groups`
- `/users`
- `/broadcast`

## Important notes
- Telegram Bot API does not allow arbitrary inline-button colors. Button appearance depends on the Telegram client.
- Each user must press Start in the bot's private chat before the bot can DM them rewards.
- Global broadcast is sent to users who have started the bot and groups where the bot is recorded active. Respect group rules and user consent; avoid unwanted or excessive messages.
- Proof review is manual in this starter version. Owner approves or rejects from Admin Panel -> Pending Proofs.
- Reward delivery is private. If a user has not started the bot, delivery can fail.
- For stable SQLite storage on Railway, mount a persistent Volume at `/data`.
- This is a starter implementation. Test in a private test group before public use and back up the database.
