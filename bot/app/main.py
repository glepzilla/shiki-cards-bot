from __future__ import annotations

import asyncio
import base64
import html
import json
import logging
import os
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any
from urllib.parse import urlencode, urlparse
from uuid import uuid4

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.filters import CommandStart
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultPhoto,
    InlineQueryResultsButton,
    InlineQueryResultUnion,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiohttp import ClientSession, ClientTimeout, web
from pydantic_settings import BaseSettings, SettingsConfigDict

SHIKIMORI_ORIGIN = "https://shikimori.io"
MAL_ORIGIN = "https://myanimelist.net"
JIKAN_API = "https://api.jikan.moe/v4"
ANILIST_API = "https://graphql.anilist.co"
USER_AGENT = "shiki-cards-bot/0.2"
ALLOWED_IMAGE_HOSTS = {"shikimori.io", "cdn.myanimelist.net", "s4.anilist.co"}
ALLOWED_LINK_HOSTS = {"shikimori.io", "myanimelist.net"}

JIKAN_STATUS = {
    "Currently Airing": "ongoing",
    "Finished Airing": "released",
    "Not yet aired": "anons",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    public_base_url: str
    host: str = "0.0.0.0"
    port: int = 8080
    rendered_dir: Path = Path(".cache/rendered")
    rendered_max_mb: int = 256
    search_cache_ttl: int = 300
    proxy_url: str | None = None


@dataclass(frozen=True, slots=True)
class Anime:
    id: int
    name: str
    russian: str | None
    kind: str | None
    score: str | None
    status: str | None
    image_url: str | None
    image_preview: str | None
    episodes: int | None
    year: int | None
    genres: tuple[str, ...]
    source: str  # "shikimori" | "mal"

    @property
    def title(self) -> str:
        return self.russian or self.name

    @property
    def page_url(self) -> str:
        if self.source == "mal":
            return f"{MAL_ORIGIN}/anime/{self.id}"
        return f"{SHIKIMORI_ORIGIN}/animes/{self.id}"

    @classmethod
    def from_shikimori(cls, raw: dict[str, Any]) -> Anime:
        image = raw.get("image") or {}
        aired_on = str(raw.get("aired_on") or "")
        year = int(aired_on[:4]) if aired_on[:4].isdigit() else None
        score = raw.get("score")
        return cls(
            id=int(raw["id"]),
            name=str(raw.get("name") or "Untitled"),
            russian=raw.get("russian") or None,
            kind=raw.get("kind"),
            score=None if not score or str(score) == "0.0" else str(score),
            status=raw.get("status"),
            image_url=absolute_url(image.get("original") or image.get("preview")),
            image_preview=absolute_url(image.get("preview") or image.get("original")),
            episodes=raw.get("episodes") or raw.get("episodes_aired") or None,
            year=year,
            genres=(),
            source="shikimori",
        )

    @classmethod
    def from_jikan(cls, raw: dict[str, Any]) -> Anime:
        images = (raw.get("images") or {}).get("jpg") or {}
        kind = raw.get("type")
        score = raw.get("score")
        genres = tuple(str(genre["name"]) for genre in raw.get("genres") or [] if genre.get("name"))
        return cls(
            id=int(raw["mal_id"]),
            name=str(raw.get("title") or "Untitled"),
            russian=None,
            kind=str(kind).lower() if kind else None,
            score=str(score) if score else None,
            status=JIKAN_STATUS.get(str(raw.get("status") or "")),
            image_url=images.get("large_image_url") or images.get("image_url"),
            image_preview=images.get("image_url") or images.get("large_image_url"),
            episodes=raw.get("episodes"),
            year=raw.get("year"),
            genres=genres,
            source="mal",
        )


class TTLCache[T]:
    """Tiny in-memory TTL cache so repeated requests don't hammer external APIs."""

    def __init__(self, ttl: float, max_entries: int = 128) -> None:
        self._ttl = ttl
        self._max_entries = max_entries
        self._entries: OrderedDict[str, tuple[float, T]] = OrderedDict()

    def get(self, key: str) -> T | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        stored_at, value = entry
        if monotonic() - stored_at > self._ttl:
            del self._entries[key]
            return None
        return value

    def put(self, key: str, value: T) -> None:
        self._entries[key] = (monotonic(), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)


class Throttle:
    """Min interval between calls to one upstream host — their rate limits are strict
    (Shikimori 5 rps, Jikan 3 rps), and a fast typer easily bursts past them."""

    def __init__(self, interval: float) -> None:
        self._interval = interval
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            delay = self._last + self._interval - monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
            self._last = monotonic()


THROTTLES = {
    "shikimori.io": Throttle(0.35),
    "api.jikan.moe": Throttle(0.5),
    "graphql.anilist.co": Throttle(0.35),
}


async def fetch_json(
    session: ClientSession,
    url: str,
    *,
    params: dict[str, str] | None = None,
    json_payload: dict[str, Any] | None = None,
) -> Any:
    """GET/POST JSON with per-host throttling and a single retry on 429."""
    throttle = THROTTLES.get(urlparse(url).netloc)
    headers = {"User-Agent": USER_AGENT}
    method = "POST" if json_payload is not None else "GET"
    for attempt in (0, 1):
        if throttle:
            await throttle.wait()
        async with session.request(
            method, url, params=params, json=json_payload, headers=headers
        ) as resp:
            if resp.status == 429 and attempt == 0:
                try:
                    delay = float(resp.headers.get("Retry-After") or 1.0)
                except ValueError:
                    delay = 1.0
                logging.warning("429 from %s, retrying in %.1fs", url, delay)
                await asyncio.sleep(min(delay, 3.0))
                continue
            resp.raise_for_status()
            return await resp.json()
    raise RuntimeError("unreachable")


def absolute_url(path: str | None) -> str | None:
    if not path:
        return None
    if path.startswith("http"):
        return path
    if path.startswith("//"):
        return f"https:{path}"
    return f"{SHIKIMORI_ORIGIN}{path}"


def allowed_link(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc in ALLOWED_LINK_HOSTS


async def search_shikimori(session: ClientSession, query: str) -> list[Anime]:
    params = {"search": query, "limit": "10", "order": "popularity"}
    data = await fetch_json(session, f"{SHIKIMORI_ORIGIN}/api/animes", params=params)
    return [Anime.from_shikimori(item) for item in data]


async def search_jikan(session: ClientSession, query: str) -> list[Anime]:
    params = {"q": query, "limit": "10", "order_by": "members", "sort": "desc", "sfw": "true"}
    data = await fetch_json(session, f"{JIKAN_API}/anime", params=params)
    return [Anime.from_jikan(item) for item in data.get("data") or []]


async def search_anime(
    session: ClientSession, cache: TTLCache[list[Anime]], query: str
) -> list[Anime]:
    """Shikimori first (Russian titles), Jikan as fallback when it fails or finds nothing."""
    key = query.casefold()
    cached = cache.get(key)
    if cached is not None:
        return cached

    animes: list[Anime] = []
    shikimori_failed = False
    try:
        animes = await search_shikimori(session, query)
    except Exception:
        shikimori_failed = True
        logging.warning("Shikimori search failed, trying Jikan", exc_info=True)

    if not animes:
        try:
            animes = await search_jikan(session, query)
        except Exception:
            if shikimori_failed:
                raise
            logging.warning("Jikan fallback failed", exc_info=True)

    cache.put(key, animes)
    return animes


async def fetch_shikimori_genres(session: ClientSession, anime_id: int) -> list[str]:
    data = await fetch_json(session, f"{SHIKIMORI_ORIGIN}/api/animes/{anime_id}")
    genres = [
        str(genre.get("russian") or genre.get("name") or "") for genre in data.get("genres") or []
    ]
    return [genre for genre in genres if genre][:4]


async def trending_anime(session: ClientSession, cache: TTLCache[list[Anime]]) -> list[Anime]:
    """Top-ranked currently airing shows for the webapp idle screen."""
    cached = cache.get("__trending__")
    if cached is not None:
        return cached

    animes: list[Anime] = []
    try:
        params = {"limit": "10", "order": "ranked", "status": "ongoing"}
        data = await fetch_json(session, f"{SHIKIMORI_ORIGIN}/api/animes", params=params)
        animes = [Anime.from_shikimori(item) for item in data]
    except Exception:
        logging.warning("Shikimori trending failed, trying Jikan", exc_info=True)

    if not animes:
        try:
            params = {"filter": "airing", "limit": "10"}
            data = await fetch_json(session, f"{JIKAN_API}/top/anime", params=params)
            animes = [Anime.from_jikan(item) for item in data.get("data") or []]
        except Exception:
            logging.warning("Jikan trending fallback failed", exc_info=True)

    if animes:
        cache.put("__trending__", animes)
    return animes


async def fetch_jikan_pictures(session: ClientSession, mal_id: int) -> list[tuple[str, str]]:
    data = await fetch_json(session, f"{JIKAN_API}/anime/{mal_id}/pictures")
    pairs: list[tuple[str, str]] = []
    for item in data.get("data") or []:
        jpg = item.get("jpg") or {}
        url = jpg.get("large_image_url") or jpg.get("image_url")
        thumb = jpg.get("image_url") or jpg.get("large_image_url")
        if url:
            pairs.append((str(url), str(thumb or url)))
    return pairs


async def fetch_anilist_cover(session: ClientSession, mal_id: int) -> list[tuple[str, str]]:
    query = "query($id:Int){Media(idMal:$id,type:ANIME){coverImage{extraLarge large}}}"
    payload = {"query": query, "variables": {"id": mal_id}}
    data = await fetch_json(session, ANILIST_API, json_payload=payload)
    cover = ((data.get("data") or {}).get("Media") or {}).get("coverImage") or {}
    url = cover.get("extraLarge") or cover.get("large")
    thumb = cover.get("large") or cover.get("extraLarge")
    return [(str(url), str(thumb or url))] if url else []


def allowed_image(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc in ALLOWED_IMAGE_HOSTS


async def collect_posters(session: ClientSession, mal_id: int) -> list[dict[str, str]]:
    """Alternative posters from AniList and Jikan (MAL pictures); Shikimori ids match MAL ids."""
    results = await asyncio.gather(
        fetch_anilist_cover(session, mal_id),
        fetch_jikan_pictures(session, mal_id),
        return_exceptions=True,
    )
    posters: list[dict[str, str]] = []
    seen: set[str] = set()
    for source, result in zip(("anilist", "mal"), results, strict=True):
        if isinstance(result, BaseException):
            logging.warning("poster fetch (%s) failed for %s: %s", source, mal_id, result)
            continue
        for url, thumb in result:
            if not allowed_image(url) or url in seen:
                continue
            seen.add(url)
            posters.append(
                {"url": url, "thumb": thumb if allowed_image(thumb) else url, "source": source}
            )
    return posters[:12]


def parse_card_query(text: str) -> str | None:
    text = text.strip()
    if not text.startswith("card:"):
        return None
    card_id = text.removeprefix("card:")
    if not card_id or not all(ch.isalnum() or ch == "-" for ch in card_id):
        return None
    return card_id


def anime_payload(anime: Anime) -> dict[str, Any]:
    return {
        "id": anime.id,
        "name": anime.name,
        "title": anime.title,
        "kind": anime.kind,
        "score": anime.score,
        "status": anime.status,
        "image_url": anime.image_url,
        "image_preview": anime.image_preview,
        "episodes": anime.episodes,
        "year": anime.year,
        "genres": list(anime.genres),
        "source": anime.source,
        "page_url": anime.page_url,
    }


def description(anime: Anime, ru: bool = True) -> str:
    parts = [anime.name]
    meta = []
    if anime.score:
        meta.append(f"⭐ {anime.score}")
    if anime.kind:
        meta.append(anime.kind.upper())
    if anime.year:
        meta.append(str(anime.year))
    if anime.episodes:
        meta.append(f"{anime.episodes} эп." if ru else f"{anime.episodes} ep.")
    if meta:
        parts.append(" · ".join(meta))
    return "\n".join(parts)


def caption(anime: Anime) -> str:
    text = f"<b>{html.escape(anime.title)}</b>"
    if anime.name != anime.title:
        text += f"\n{html.escape(anime.name)}"
    meta = []
    if anime.score:
        meta.append(f"⭐ {html.escape(anime.score)}")
    if anime.kind:
        meta.append(html.escape(anime.kind.upper()))
    if anime.year:
        meta.append(str(anime.year))
    if meta:
        text += f"\n{' · '.join(meta)}"
    return text


def card_caption(meta: dict[str, Any]) -> str:
    title = str(meta.get("title") or "").strip()
    if not title:
        return "Rendered with Shiki Cards"
    text = f"<b>{html.escape(title)}</b>"
    subtitle = str(meta.get("subtitle") or "").strip()
    if subtitle and subtitle != title:
        text += f"\n{html.escape(subtitle)}"
    return text


def card_markup(meta: dict[str, Any]) -> InlineKeyboardMarkup | None:
    url = str(meta.get("url") or "")
    if not allowed_link(url):
        return None
    label = "Открыть на MyAnimeList" if "myanimelist" in url else "Открыть на Shikimori"
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text=label, url=url)]])


def anime_markup(anime: Anime) -> InlineKeyboardMarkup:
    label = "Открыть на MyAnimeList" if anime.source == "mal" else "Открыть на Shikimori"
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text=label, url=anime.page_url)]]
    )


def load_card_meta(settings: Settings, card_id: str) -> dict[str, Any]:
    meta_path = settings.rendered_dir / f"{card_id}.json"
    try:
        loaded = json.loads(meta_path.read_text())
        return loaded if isinstance(loaded, dict) else {}
    except OSError, ValueError:
        return {}


def webapp_url(settings: Settings, query_text: str = "") -> str:
    params = {}
    if query_text and not parse_card_query(query_text):
        params["q"] = query_text
    query = f"?{urlencode(params)}" if params else ""
    return f"{settings.public_base_url.rstrip('/')}/webapp{query}"


def results_button(
    settings: Settings, query_text: str = "", ru: bool = True
) -> InlineQueryResultsButton:
    return InlineQueryResultsButton(
        text="🎨 Собрать карточку в WebApp" if ru else "🎨 Build a card in WebApp",
        web_app=WebAppInfo(url=webapp_url(settings, query_text)),
    )


def is_russian(language_code: str | None) -> bool:
    return not language_code or language_code.startswith("ru")


START_TEXT_RU = (
    "Привет! Я собираю красивые карточки аниме.\n\n"
    "1. Открой конструктор кнопкой ниже или напиши <code>@{username} название</code> "
    "в любом чате\n"
    "2. Выбери аниме, постер и стиль\n"
    "3. Отправь карточку друзьям"
)
START_TEXT_EN = (
    "Hi! I build pretty anime cards.\n\n"
    "1. Open the builder below or type <code>@{username} title</code> in any chat\n"
    "2. Pick an anime, a poster and a style\n"
    "3. Share the card with friends"
)


def cleanup_rendered_dir(settings: Settings) -> None:
    max_bytes = settings.rendered_max_mb * 1024 * 1024
    if max_bytes <= 0 or not settings.rendered_dir.exists():
        return

    files: list[tuple[float, int, Path]] = []
    total = 0
    for path in settings.rendered_dir.glob("*.jpg"):
        try:
            stat = path.stat()
        except OSError:
            continue
        total += stat.st_size
        files.append((stat.st_mtime, stat.st_size, path))

    if total <= max_bytes:
        return

    files.sort()  # oldest first
    target = int(max_bytes * 0.9)
    removed = 0
    for _, size, path in files:
        if total <= target:
            break
        try:
            path.unlink()
        except OSError:
            continue
        path.with_suffix(".json").unlink(missing_ok=True)
        total -= size
        removed += 1

    if removed:
        logging.info(
            "rendered cleanup removed %s files; current size %.2f MB / limit %s MB",
            removed,
            total / 1024 / 1024,
            settings.rendered_max_mb,
        )


def build_router(
    settings: Settings, session: ClientSession, cache: TTLCache[list[Anime]]
) -> Router:
    router = Router()

    @router.message(CommandStart())
    async def start(message: Message) -> None:
        ru = is_russian(message.from_user.language_code if message.from_user else None)
        username = (await message.bot.me()).username if message.bot else "bot"
        template = START_TEXT_RU if ru else START_TEXT_EN
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎨 Открыть конструктор" if ru else "🎨 Open the builder",
                        web_app=WebAppInfo(url=webapp_url(settings)),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💬 Попробовать в чате" if ru else "💬 Try inline",
                        switch_inline_query="",
                    )
                ],
            ]
        )
        await message.answer(
            template.format(username=username), parse_mode="HTML", reply_markup=keyboard
        )

    @router.inline_query()
    async def inline_search(query: InlineQuery) -> None:
        text = query.query.strip()
        ru = is_russian(query.from_user.language_code)
        card_id = parse_card_query(text)

        if card_id:
            image_path = settings.rendered_dir / f"{card_id}.jpg"
            if not image_path.exists():
                await query.answer(
                    [], cache_time=1, is_personal=True, button=results_button(settings, text, ru)
                )
                return
            meta = load_card_meta(settings, card_id)
            image_url = f"{settings.public_base_url.rstrip('/')}/rendered/{card_id}.jpg"
            await query.answer(
                [
                    InlineQueryResultPhoto(
                        id=f"card-{card_id}",
                        photo_url=image_url,
                        thumbnail_url=image_url,
                        title=str(meta.get("title") or "Shiki Card"),
                        description="Готовая карточка из WebApp" if ru else "Card from the WebApp",
                        # caption=card_caption(meta),
                        parse_mode="HTML",
                        reply_markup=card_markup(meta),
                    )
                ],
                cache_time=300,
                is_personal=True,
                button=results_button(settings, text, ru),
            )
            return

        if len(text) < 2:
            await query.answer(
                [], cache_time=1, is_personal=True, button=results_button(settings, text, ru)
            )
            return

        try:
            animes = await search_anime(session, cache, text)
        except Exception:
            logging.exception("anime search failed")
            await query.answer(
                [], cache_time=1, is_personal=True, button=results_button(settings, text, ru)
            )
            return

        results: list[InlineQueryResultUnion] = []
        for anime in animes:
            if not anime.image_url:
                continue
            results.append(
                InlineQueryResultPhoto(
                    id=f"{anime.source}-{anime.id}",
                    photo_url=anime.image_url,
                    thumbnail_url=anime.image_preview or anime.image_url,
                    title=anime.title,
                    description=description(anime, ru),
                    caption=caption(anime),
                    parse_mode="HTML",
                    reply_markup=anime_markup(anime),
                )
            )

        await query.answer(
            results,
            cache_time=30,
            is_personal=True,
            button=results_button(settings, text, ru),
        )

    return router


async def create_web_app(
    settings: Settings, session: ClientSession, cache: TTLCache[list[Anime]]
) -> web.Application:
    settings.rendered_dir.mkdir(parents=True, exist_ok=True)
    app = web.Application(client_max_size=8 * 1024 * 1024)
    html_path = Path(__file__).with_name("webapp.html")
    poster_cache: TTLCache[list[dict[str, str]]] = TTLCache(ttl=3600)
    trending_cache: TTLCache[list[Anime]] = TTLCache(ttl=1800)

    async def webapp_page(_: web.Request) -> web.FileResponse:
        return web.FileResponse(html_path)

    async def search_api(request: web.Request) -> web.Response:
        query = (request.query.get("q") or "").strip()
        if len(query) < 2:
            return web.json_response([])
        try:
            animes = await search_anime(session, cache, query)
        except Exception:
            logging.exception("webapp search failed")
            return web.json_response([], status=500)
        return web.json_response([anime_payload(anime) for anime in animes])

    async def trending_api(_: web.Request) -> web.Response:
        animes = await trending_anime(session, trending_cache)
        return web.json_response([anime_payload(anime) for anime in animes])

    async def genres_api(request: web.Request) -> web.Response:
        raw_id = request.match_info["anime_id"]
        if not raw_id.isdigit() or request.query.get("source", "shikimori") != "shikimori":
            return web.json_response({"genres": []})
        try:
            genres = await fetch_shikimori_genres(session, int(raw_id))
        except Exception:
            logging.warning("genres fetch failed for %s", raw_id, exc_info=True)
            genres = []
        return web.json_response({"genres": genres})

    async def posters_api(request: web.Request) -> web.Response:
        raw_id = request.match_info["anime_id"]
        if not raw_id.isdigit():
            return web.json_response({"posters": []})
        cached = poster_cache.get(raw_id)
        if cached is not None:
            return web.json_response({"posters": cached})
        posters = await collect_posters(session, int(raw_id))
        poster_cache.put(raw_id, posters)
        return web.json_response({"posters": posters})

    async def image_proxy(request: web.Request) -> web.Response:
        url = request.query.get("url") or ""
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in ALLOWED_IMAGE_HOSTS:
            return web.Response(status=400, text="image host not allowed")
        async with session.get(url, headers={"User-Agent": USER_AGENT}) as resp:
            resp.raise_for_status()
            body = await resp.read()
            content_type = resp.headers.get("Content-Type", "image/jpeg")
        return web.Response(
            body=body,
            content_type=content_type,
            headers={
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=86400, immutable",
            },
        )

    async def save_rendered(request: web.Request) -> web.Response:
        payload = await request.json()
        data_url = str(payload.get("image") or "")
        if not data_url.startswith("data:image/jpeg;base64,"):
            return web.json_response({"ok": False, "error": "jpeg data url required"}, status=400)
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        except ValueError:
            return web.json_response({"ok": False, "error": "bad base64"}, status=400)
        if len(raw) > 5 * 1024 * 1024:
            return web.json_response({"ok": False, "error": "image too large"}, status=400)

        meta_raw = payload.get("meta") or {}
        url = str(meta_raw.get("url") or "")
        meta = {
            "title": str(meta_raw.get("title") or "")[:200],
            "subtitle": str(meta_raw.get("subtitle") or "")[:200],
            "url": url if allowed_link(url) else "",
        }

        card_id = uuid4().hex
        (settings.rendered_dir / f"{card_id}.jpg").write_bytes(raw)
        (settings.rendered_dir / f"{card_id}.json").write_text(json.dumps(meta, ensure_ascii=False))
        cleanup_rendered_dir(settings)
        return web.json_response({"ok": True, "id": card_id, "query": f"card:{card_id}"})

    app.router.add_get("/webapp", webapp_page)
    app.router.add_get("/api/search", search_api)
    app.router.add_get("/api/trending", trending_api)
    app.router.add_get("/api/anime/{anime_id}/genres", genres_api)
    app.router.add_get("/api/anime/{anime_id}/posters", posters_api)
    app.router.add_get("/api/image", image_proxy)
    app.router.add_post("/api/rendered", save_rendered)
    app.router.add_static(
        "/rendered/", path=settings.rendered_dir, name="rendered", show_index=False
    )
    return app


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    settings = Settings()  # type: ignore[call-arg]  # fields come from env/.env
    cleanup_rendered_dir(settings)
    cache: TTLCache[list[Anime]] = TTLCache(ttl=settings.search_cache_ttl)

    async with ClientSession(
        timeout=ClientTimeout(total=15), trust_env=settings.proxy_url is not None
    ) as session:
        bot_session = AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None
        bot = Bot(settings.bot_token, session=bot_session)
        dp = Dispatcher()
        dp.include_router(build_router(settings, session, cache))

        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Карточки", web_app=WebAppInfo(url=webapp_url(settings))
                )
            )
        except Exception:
            logging.warning("failed to set chat menu button", exc_info=True)

        web_app = await create_web_app(settings, session, cache)
        runner = web.AppRunner(web_app)
        await runner.setup()
        site = web.TCPSite(runner, settings.host, settings.port)
        await site.start()
        logging.info("webapp listening on %s:%s", settings.host, settings.port)

        try:
            await dp.start_polling(bot)
        finally:
            await runner.cleanup()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
