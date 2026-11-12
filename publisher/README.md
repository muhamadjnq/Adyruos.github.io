# Adyru publisher

- `queue/telegram/*.json` — approved Telegram posts. GitHub Actions (`.github/workflows/telegram-publisher.yml`) posts each one through the Telegram Bot API at its `send_at` time.
- `state/telegram.json` — what was sent (message id, link) so nothing is posted twice.
- `media/` — public image/video files used by the Instagram and LinkedIn APIs (Supermetrics) and by Telegram.

Required repository secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` (for example `@adyru`).
Items are written here only after approval in the Adyru publishing desk.
