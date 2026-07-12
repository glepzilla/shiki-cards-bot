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
- **Метаданные карточки** — рядом с JPEG сохраняется JSON с названием и ссылкой, поэтому у готовой карточки есть подпись и кнопка.

## Run locally

```bash
cp .env.example .env
# Fill BOT_TOKEN and PUBLIC_BASE_URL
devbox shell
PYTHONPATH=bot uv run python -m app.main
```

`PUBLIC_BASE_URL` нужен для WebApp и для URL готовых картинок. В локальной разработке можно открыть `PORT=8080` через ngrok/cloudflared и указать HTTPS origin.

Docker:

```bash
docker compose up --build
```

## How it works

1. Пользователь пишет `@your_bot query`.
2. Бот ищет на Shikimori (фолбэк — Jikan) и отвечает результатами + верхней кнопкой → WebApp.
3. В WebApp пользователь ищет аниме, тапает результат и видит живой предпросмотр карточки.
4. Пресеты переключают стиль рендера прямо в Canvas; жанры дорисовываются, когда придут с API.
5. По кнопке «Отправить карточку» WebApp загружает JPEG + метаданные на `/api/rendered`.
6. WebApp вызывает `Telegram.WebApp.switchInlineQuery('card:<id>', ...)`.
7. Бот получает `card:<id>` и возвращает `InlineQueryResultPhoto` с URL `/rendered/<id>.jpg`, подписью и кнопкой из метаданных.

Нет кеша через storage chat и нет загрузки файла ботом в Telegram для получения `file_id`.

## API surface (webapp server)

| Route | What |
|---|---|
| `GET /webapp` | single-file WebApp |
| `GET /api/search?q=` | поиск (Shikimori → Jikan), JSON |
| `GET /api/trending` | топ онгоингов для пустого экрана |
| `GET /api/anime/{id}/genres` | жанры с Shikimori для карточки |
| `GET /api/anime/{id}/posters` | альтернативные постеры (AniList + Jikan), id = MAL id |
| `GET /api/image?url=` | прокси картинок (shikimori.one, cdn.myanimelist.net, s4.anilist.co) |
| `POST /api/rendered` | приём готового JPEG + метаданных |
| `GET /rendered/{id}.jpg` | статика готовых карточек |

## Rendered files cleanup

Готовые картинки лежат в `RENDERED_DIR` (JPEG + JSON с метаданными). Когда общий размер `*.jpg` превышает `RENDERED_MAX_MB`, сервер удаляет самые старые файлы (вместе с их JSON), пока размер не опустится примерно до 90% лимита. Очистка запускается при старте и после сохранения новой карточки.

## Dev

```bash
uv run ruff check bot
uv run mypy
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
