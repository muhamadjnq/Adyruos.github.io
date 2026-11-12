# Adyru publisher

- `queue/telegram/*.json` — approved Telegram posts (Telegram Bot API).
- `queue/linkedin/*.json` — approved LinkedIn posts for the Adyru company page (sent at `send_at` to a Make.com webhook that posts as the page).
- Both run in GitHub Actions (`.github/workflows/telegram-publisher.yml`) every 10 minutes; each item is posted on the minute of its `send_at`.
- `state/telegram.json` — what was sent (message id, link) so nothing is posted twice.
- `media/` — public image/video files used by the Instagram and LinkedIn APIs (Supermetrics) and by Telegram.

Required repository secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (for example `@adyru`), `MAKE_LINKEDIN_WEBHOOK`.
Items are written here only after approval in the Adyru publishing desk.
