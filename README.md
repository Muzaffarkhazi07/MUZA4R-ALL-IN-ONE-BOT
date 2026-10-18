# MUZA4R ALL-IN-ONE BOT

## Included
- Inline menu and `/help`
- QR generator, random password generator, text formatter
- Public website status checker with local/private IP blocking
- Basic URL information
- AI chat with owner-configurable OpenAI or Gemini API
- AI response footer: Created by Muzaffar [MUZA4R]
- Owner panel, user list, ban/unban, activity logs, file log
- Owner-editable welcome text and welcome media
- Supported uploaded media/files copied to owner
- SQLite persistence (configure `DB_PATH` on a persistent Railway volume)

## Important privacy behavior
- The bot displays a notice before a user can use it.
- Users choose whether to allow detailed content logging for text/AI features.
- Generated password values are never logged or sent to the owner.
- Files sent to the bot are copied to the owner, and the notice says so.
- Do not use this bot to collect sensitive personal information.

## Setup
1. Create a bot with Telegram's official BotFather and copy its token.
2. Upload `bot.py`, `requirements.txt`, and `Procfile` to your GitHub repository.
3. In Railway, create a service from the repository.
4. Add variables:
   - `BOT_TOKEN` = your Telegram bot token
   - `OWNER_ID` = `5839586443`
   - `DB_PATH` = `/data/muza4r_bot.sqlite3` (if `/data` is your mounted persistent volume)
5. Attach a persistent volume mounted at `/data`.
6. Deploy. Start a private chat with your bot and send `/start`.

## Add AI API later
1. Send `/admin` from the owner account.
2. Open AI API Settings.
3. Select OpenAI or Gemini.
4. Tap Add/Replace API Key and send the key in the private owner chat.
5. Set the model if needed, then use Test Configuration.

The key is stored in SQLite in this starter version. For stronger security, prefer hosting secret variables or implement encryption/key management. Never share your API key.

## Commands
General: `/start`, `/help`, `/profile`, `/stats`, `/qr`, `/password`, `/format`, `/webcheck`, `/urlinfo`, `/ai`, `/files`
Owner: `/admin`, `/users`, `/logs`, `/ban USER_ID`, `/unban USER_ID`, `/apistatus`

## Notes
- This is a starter implementation. Test it with a private test bot before sharing publicly.
- Telegram bots cannot read private chats/files outside messages sent to the bot.
- For very large albums, Telegram delivers separate messages; each supported media message is copied individually.
