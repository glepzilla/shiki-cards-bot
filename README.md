<p align="center">
  <img src="bot/app/static/shikizilla-logo.png" width="220" alt="Логотип Shikizilla" />
</p>

<h1 align="center">Shikizilla</h1>

<p align="center">
  Конструктор аниме-карточек для браузера и Telegram.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Telegram-WebApp-26A5E4?logo=telegram&logoColor=white" alt="Telegram WebApp" />
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white" alt="Python 3.14" />
  <a href="https://github.com/glepzilla/shiki-cards-bot/actions/workflows/deploy.yml"><img src="https://github.com/glepzilla/shiki-cards-bot/actions/workflows/deploy.yml/badge.svg" alt="Deploy" /></a>
</p>

<p align="center">
  <a href="https://shiki.glepzilla.ru">Открыть Shikizilla</a>
</p>

Shikizilla помогает найти аниме, выбрать постер и собрать готовую карточку. В обычном
браузере её можно скачать в JPEG, а внутри Telegram — сразу отправить в чат.

## Возможности

- поиск аниме и актуальная подборка на главном экране;
- несколько источников и вариантов постеров;
- девять стилей карточек;
- настройка названия, оценки, жанров и подписи;
- скачивание в браузере и отправка через Telegram.

## Локальный запуск

Понадобятся [Python 3.14](https://www.python.org/) и
[uv](https://docs.astral.sh/uv/).

```bash
git clone https://github.com/glepzilla/shiki-cards-bot.git
cd shiki-cards-bot
cp .env.example .env
uv sync --all-groups
```

Заполните три значения в `.env`:

| Переменная | Что указать |
| --- | --- |
| `BOT_TOKEN` | Токен бота от [@BotFather](https://t.me/BotFather) |
| `PUBLIC_BASE_URL` | Публичный HTTPS-адрес без дополнительного пути |
| `STORAGE_CHAT_ID` | ID закрытого канала, куда бот может отправлять изображения |

Для Telegram понадобится HTTPS-туннель до локального порта `8080`, например ngrok или
Cloudflare Tunnel. После этого запустите приложение:

```bash
PYTHONPATH=bot uv run python -m app.main
```

В браузере Shikizilla будет доступна по адресу <http://localhost:8080>.

### Через Docker

После заполнения `.env`:

```bash
docker compose -f compose.dev.yml up --build
```

## Проверка изменений

```bash
uv run ruff check bot tests
uv run mypy
PYTHONPATH=bot uv run pytest
```
