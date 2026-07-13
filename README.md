# Shiki Cards Bot

Telegram inline-бот и WebApp для поиска аниме на Shikimori и создания карточек для отправки в чаты.

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

## Продакшен: сеть и split egress

В Cloudflare для домена приложения используйте **DNS only** (серое облако), а TLS
терминируйте на origin через Traefik/Let's Encrypt. После переключения проверьте
`https://<домен>/healthz` из обычного браузера и из Telegram.

`PROXY_URL` нужен только для заблокированных исходящих направлений: Bot API,
Jikan, AniList и CDN MAL/AniList. Shikimori (`shikimori.one`) приложение вызывает
напрямую. Не задавайте `HTTP_PROXY` или `HTTPS_PROXY` контейнеру: они возвращают
Shikimori в медленный глобальный туннель. На Docker-хосте Clash/Mihomo должен
слушать bridge-доступный `host.docker.internal:7890`.

После деплоя проверьте health endpoint и Telegram Bot API через прокси:

```bash
curl --fail https://<домен>/healthz
# Бот должен отвечать на /start; это также подтверждает getMe/polling через PROXY_URL.
```
