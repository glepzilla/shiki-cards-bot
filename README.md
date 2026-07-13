# Shiki Cards Bot

Telegram inline-бот и WebApp для поиска аниме на Shikimori (с фолбэком на Jikan) и создания карточек для отправки в чаты.

## Локальный запуск

Требуются Python 3.14 и [uv](https://docs.astral.sh/uv/).

```bash
cp .env.example .env
# Заполните BOT_TOKEN, PUBLIC_BASE_URL и STORAGE_CHAT_ID
uv sync --all-groups
PYTHONPATH=bot uv run python -m app.main
```

`PUBLIC_BASE_URL` должен быть публичным HTTPS-адресом: Telegram открывает по нему WebApp. Для локальной разработки используйте туннель, например ngrok или Cloudflare Tunnel.

### Docker

```bash
cp .env.example .env
# Заполните BOT_TOKEN, PUBLIC_BASE_URL и STORAGE_CHAT_ID
docker compose -f compose.dev.yml up --build
```
