# Shiki Cards Bot

Inline Telegram bot: ищет аниме на Shikimori (с фолбэком на Jikan/MyAnimeList) и собирает красивые карточки в Telegram WebApp прямо на устройстве.

## Stack

- Python 3.14, aiogram, aiohttp
- Shikimori API + Jikan API (фолбэк)
- Telegram WebApp + Canvas render (без сервера рендеринга)
- devbox / Docker

## Features

- **Inline-поиск** — `@your_bot название` отдаёт постеры с оценкой, годом, числом эпизодов и кнопкой на страницу аниме; миниатюры лёгкие (preview-размер).
- **WebApp-конструктор** — поиск, живой предпросмотр карточки на Canvas и девять стилей: Классика, Аврора, Стекло, Неон, VHS, Манга, Журнал, Полароид, Принт. Зум по тапу, переключатель RU/оригинального названия, тогглы оценки/жанров/подписи.
- **`/start` и кнопка меню** — онбординг с кнопками «Открыть конструктор» и «Попробовать в чате», WebApp доступен из меню чата.
- **Экран без запроса** — история поиска (localStorage) и «Сейчас смотрят» (топ онгоингов, Shikimori → Jikan фолбэк, кеш 30 мин).
- **Нативный шеринг** — Telegram MainButton с прогрессом; вне Telegram — кнопка «Скачать JPEG».
- **Jikan-фолбэк** — если Shikimori лежит или ничего не нашёл, поиск уходит в Jikan (MyAnimeList).
- **Выбор постера** — альтернативные постеры с MyAnimeList (Jikan pictures) и обложка с AniList; миниатюры в превью, тап перерисовывает карточку.
- **Жанры на карточке** — подтягиваются отдельным запросом и рисуются чипсами.
- **TTL-кеш поиска** — повторные inline-запросы не бьют по внешним API.
- **Telegram `file_id` для карточек** — бот загружает JPEG в служебный чат и отвечает `InlineQueryResultCachedPhoto`; отправка не зависит от Cloudflare/origin и переживает локальную LRU-очистку.
- **Дедупликация рендера** — одинаковый JPEG определяется по SHA-256 и переиспользует существующий `file_id` без повторной загрузки в Telegram.
- **Защищённый WebApp API** — запросы несут Telegram `initData`, которое сервер проверяет HMAC-ом; JPEG дополнительно проверяется по magic bytes.

## Run locally

```bash
cp .env.example .env
# Fill BOT_TOKEN, PUBLIC_BASE_URL and STORAGE_CHAT_ID
devbox shell
PYTHONPATH=bot uv run python -m app.main
```

`PUBLIC_BASE_URL` нужен для WebApp. `STORAGE_CHAT_ID` — ID приватного служебного чата/канала, куда бот добавлен с правом отправлять сообщения: туда загружаются карточки для получения Telegram `file_id`. В локальной разработке можно открыть `PORT=8080` через ngrok/cloudflared и указать HTTPS origin.

Docker:

```bash
docker compose up --build
```

## How it works

1. Пользователь пишет `@your_bot query`.
2. Бот ищет на Shikimori (фолбэк — Jikan) и отвечает результатами + верхней кнопкой → WebApp.
3. В WebApp пользователь ищет аниме, тапает результат и видит живой предпросмотр карточки.
4. Пресеты переключают стиль рендера прямо в Canvas; жанры дорисовываются, когда придут с API.
5. По кнопке «Отправить карточку» WebApp загружает JPEG + метаданные на защищённый `/api/rendered`.
6. Бот отправляет JPEG в `STORAGE_CHAT_ID`, получает `file_id` и сохраняет его в метаданных карточки.
7. WebApp вызывает `Telegram.WebApp.switchInlineQuery('card:<id>', ...)`.
8. Бот получает `card:<id>` и возвращает `InlineQueryResultCachedPhoto` с постоянным Telegram `file_id`, подписью и кнопкой из метаданных.

## API surface (webapp server)

| Route | What |
|---|---|
| `GET /webapp` | single-file WebApp |
| `GET /api/search?q=` | поиск (Shikimori → Jikan), JSON |
| `GET /api/trending` | топ онгоингов для пустого экрана |
| `GET /api/anime/{id}/genres` | жанры с Shikimori для карточки |
| `GET /api/anime/{id}/posters` | альтернативные постеры (AniList + Jikan), id = MAL id |
| `GET /api/image?url=` | ограниченный по хостам и размеру прокси картинок |
| `POST /api/rendered` | приём JPEG + метаданных, требует `X-Telegram-Init-Data` |
| `GET /rendered/{id}.jpg` | только JPEG, с immutable cache headers; метаданные не выдаются |
| `GET /healthz` | liveness probe |

Все data API WebApp (`/api/search`, trending, genres, posters и rendered) требуют валидный `X-Telegram-Init-Data`. Его автоматически добавляет WebApp; сервер проверяет Telegram HMAC и срок действия `auth_date`.

## Rendered files cleanup

Локальные JPEG лежат в `RENDERED_DIR` только как необязательный cache/debug-артефакт. Когда общий размер `*.jpg` превышает `RENDERED_MAX_MB`, сервер удаляет самые старые JPEG до примерно 90% лимита. JSON с `file_id` не удаляется: Telegram хранит медиа, и инлайн-карточки продолжают работать после LRU-очистки.

## Dev

```bash
uv run ruff check bot
uv run mypy
uv run pytest
```

## Production proxy

The production compose file routes Telegram and external API traffic through
`http://host.docker.internal:7890`. Docker resolves this name to the host gateway.
The host proxy must therefore listen on the Docker bridge gateway, not only on
`127.0.0.1`. Find the gateway with:

```bash
docker network inspect bridge -f '{{(index .IPAM.Config 0).Gateway}}'
```

For a Clash/Mihomo proxy, set `bind-address` to that address (usually
`172.17.0.1`) and keep `mixed-port: 7890`. Do not expose this port publicly.
After changing its configuration, restart the proxy and deploy the bot:

```bash
docker compose pull
docker compose up -d --force-recreate
docker compose exec bot python - <<'PY'
import os
import urllib.request

url = f"https://api.telegram.org/bot{os.environ['BOT_TOKEN']}/getMe"
with urllib.request.urlopen(url, timeout=10) as response:
    print(response.status)
PY
```
