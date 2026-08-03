<p align="center">
  <img src="bot/app/static/shikizilla-logo.png" width="220" alt="Логотип Shikizilla" />
</p>

<h1 align="center">Shikizilla</h1>

<p align="center">
  Клиент Shikimori для Telegram с конструктором аниме-карточек.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Telegram-WebApp-26A5E4?logo=telegram&logoColor=white" alt="Telegram WebApp" />
  <img src="https://img.shields.io/badge/Python-3.14-3776AB?logo=python&logoColor=white" alt="Python 3.14" />
  <a href="https://github.com/glepzilla/shiki-cards-bot/actions/workflows/deploy.yml"><img src="https://github.com/glepzilla/shiki-cards-bot/actions/workflows/deploy.yml/badge.svg" alt="Deploy" /></a>
</p>

<p align="center">
  <a href="https://shiki.glepzilla.ru">Открыть Shikizilla</a>
</p>

Shikizilla помогает находить аниме, вести библиотеку Shikimori, отмечать серии и
управлять оценками прямо в Telegram. Из любого тайтла можно собрать карточку: в
браузере скачать её в JPEG, а внутри Telegram — сразу отправить в чат.

## Возможности

- каталог, поиск и актуальная подборка на главном экране;
- вход через Shikimori и библиотека по всем статусам;
- управление статусом, оценкой и прогрессом просмотра;
- профиль со сводной статистикой и оценками друзей;
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

### Интеграция с Shikimori

Создайте OAuth-приложение в [настройках Shikimori](https://shikimori.io/oauth/applications)
и добавьте в него callback URL:

```text
https://ваш-домен/oauth/shikimori/callback
```

Затем заполните в `.env` `SHIKIMORI_CLIENT_ID`, `SHIKIMORI_CLIENT_SECRET` и
`SHIKIMORI_TOKEN_KEY`. Последний ключ создаётся один раз и нужен для шифрования
токенов пользователей на диске:

```bash
uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

После перезапуска в WebApp появится раздел «Моё»: пользователь входит через
Shikimori, видит свой список «смотрю», прогресс и оценки друзей. Токены хранятся
только в зашифрованном файле `SHIKIMORI_TOKENS_FILE` и привязаны к Telegram ID.
Чтобы после входа пользователь сразу возвращался в Mini App, настройте это же
приложение как **Main Mini App** у @BotFather и укажите `BOT_USERNAME` без `@`.

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
