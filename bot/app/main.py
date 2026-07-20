from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import os
import socket
import time
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse
from uuid import uuid4

from aiogram import Bot, Dispatcher, Router
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQuery,
    InlineQueryResultCachedPhoto,
    InlineQueryResultPhoto,
    InlineQueryResultsButton,
    InlineQueryResultUnion,
    MenuButtonWebApp,
    Message,
    WebAppInfo,
)
from aiohttp import (
    ClientError,
    ClientResponseError,
    ClientSession,
    ClientTimeout,
    TCPConnector,
    web,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.cache import SlidingWindowRateLimiter, Throttle, TTLCache

SHIKIMORI_ORIGIN = "https://shikimori.io"
ANILIST_API = "https://graphql.anilist.co"
TENRAI_API = "https://api.tenrai.org/v1"
USER_AGENT = "shikizilla/0.2"
UPSTREAM_REQUEST_TIMEOUT = 4.0
INLINE_SEARCH_TIMEOUT = 8.0
TENRAI_FETCH_TIMEOUT = 8.0
TENRAI_GALLERY_TIMEOUT = 5.0
MAX_RENDERED_IMAGE_BYTES = 5 * 1024 * 1024
MAX_PROXY_IMAGE_BYTES = 5 * 1024 * 1024
ALLOWED_IMAGE_HOSTS = {
    "shikimori.one",
    "shikimori.io",  # Older API payloads may still contain this host.
    "cdn.myanimelist.net",
    "s4.anilist.co",
}
ALLOWED_LINK_HOSTS = {"shikimori.one", "shikimori.io"}
# Direct access from the VPS to AniList's Cloudflare image edge is unreliable.
PROXIED_IMAGE_HOSTS = {"s4.anilist.co"}
PROXIED_API_HOSTS = {"api.tenrai.org"}
WEBAPP_DIR = Path(__file__).parent
WEBAPP_HTML_PATH = WEBAPP_DIR / "webapp.html"
WEBAPP_STATIC_DIR = WEBAPP_DIR / "static"
WEBAPP_VERSIONED_ASSETS = (
    WEBAPP_HTML_PATH,
    WEBAPP_STATIC_DIR / "shikizilla-logo.png",
    WEBAPP_STATIC_DIR / "webapp.css",
    WEBAPP_STATIC_DIR / "webapp.js",
    WEBAPP_STATIC_DIR / "ds" / "_ds_bundle.css",
    WEBAPP_STATIC_DIR / "ds" / "_ds_bundle.js",
    WEBAPP_STATIC_DIR / "ds" / "_vendor" / "react.js",
)
_webapp_asset_digest = hashlib.sha256()
for _webapp_asset_path in WEBAPP_VERSIONED_ASSETS:
    _webapp_asset_digest.update(_webapp_asset_path.read_bytes())
WEBAPP_ASSET_VERSION = _webapp_asset_digest.hexdigest()[:12]

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    bot_token: str
    public_base_url: str
    host: str = "0.0.0.0"
    port: int = 8080
    rendered_dir: Path = Path(".cache/rendered")
    rendered_max_mb: int = 256
    search_cache_ttl: int = 300
    rendered_uploads_per_hour: int = 30
    storage_chat_id: int | str
    webapp_auth_max_age: int = 86_400
    proxy_url: str | None = None


def as_mapping(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except TypeError, ValueError:
        return None


def webapp_actor(init_data: str) -> str:
    """Stable rate-limit key without trusting it for authentication."""
    user_id = as_int(webapp_user(init_data).get("id"))
    if user_id is not None:
        return f"user:{user_id}"
    return f"init:{hashlib.sha256(init_data.encode()).hexdigest()}"


def webapp_user(init_data: str) -> dict[str, Any]:
    """Extract the user payload after initData has been authenticated by the caller."""
    try:
        user = json.loads(dict(parse_qsl(init_data, keep_blank_values=True)).get("user", ""))
        return as_mapping(user)
    except TypeError, ValueError:
        return {}


def validate_webapp_init_data(init_data: str, bot_token: str, max_age: int) -> bool:
    """Verify Telegram WebApp initData and reject expired or malformed payloads."""
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
        received_hash = pairs.pop("hash")
        auth_date = int(pairs["auth_date"])
    except KeyError, TypeError, ValueError:
        return False

    now = int(time.time())
    if auth_date > now + 300 or now - auth_date > max_age:
        return False
    data_check_string = "\n".join(f"{key}={pairs[key]}" for key in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected_hash, received_hash)


def create_inline_session(bot_token: str, user_id: int, issued_at: int | None = None) -> str:
    """Sign an inline Mini App launch, which does not receive Telegram initData."""
    timestamp = int(time.time()) if issued_at is None else issued_at
    payload = f"{user_id}.{timestamp}"
    signature = hmac.new(
        bot_token.encode(), f"InlineWebApp\n{payload}".encode(), hashlib.sha256
    ).hexdigest()
    return f"{payload}.{signature}"


def validate_inline_session(
    token: str, bot_token: str, max_age: int, now: int | None = None
) -> int | None:
    """Return the signed Telegram user id when an inline launch token is valid."""
    try:
        raw_user_id, raw_timestamp, received_signature = token.split(".", 2)
        user_id = int(raw_user_id)
        timestamp = int(raw_timestamp)
    except ValueError:
        return None
    if user_id <= 0:
        return None
    current_time = int(time.time()) if now is None else now
    if timestamp > current_time + 300 or current_time - timestamp > max_age:
        return None
    payload = f"{user_id}.{timestamp}"
    expected_signature = hmac.new(
        bot_token.encode(), f"InlineWebApp\n{payload}".encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_signature, received_signature):
        return None
    return user_id


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
    image_source: str
    episodes: int | None
    year: int | None
    genres: tuple[str, ...]
    source: str  # "shikimori"

    @property
    def title(self) -> str:
        return self.russian or self.name

    @property
    def page_url(self) -> str:
        return f"{SHIKIMORI_ORIGIN}/animes/{self.id}"

    @classmethod
    def from_shikimori(cls, raw: dict[str, Any]) -> Anime:
        image = as_mapping(raw.get("image"))
        aired_on = str(raw.get("aired_on") or "")
        year = int(aired_on[:4]) if aired_on[:4].isdigit() else None
        score = raw.get("score")
        return cls(
            id=int(raw["id"]),
            name=as_text(raw.get("name")) or "Untitled",
            russian=as_text(raw.get("russian")),
            kind=as_text(raw.get("kind")),
            score=None if not score or str(score) == "0.0" else str(score),
            status=as_text(raw.get("status")),
            image_url=absolute_url(as_text(image.get("original") or image.get("preview"))),
            image_preview=absolute_url(as_text(image.get("preview") or image.get("original"))),
            image_source="shikimori",
            episodes=as_int(raw.get("episodes")) or as_int(raw.get("episodes_aired")),
            year=year,
            genres=(),
            source="shikimori",
        )

THROTTLES = {
    "shikimori.one": Throttle(0.35),
    "shikimori.io": Throttle(0.35),
    "api.tenrai.org": Throttle(0.5),
    "graphql.anilist.co": Throttle(0.35),
}


@dataclass(frozen=True, slots=True)
class UpstreamSessions:
    """Provider session with an explicit proxy only for hosts that need it."""

    direct: ClientSession
    proxy_url: str | None = None

    def request(self, method: str, url: str, **kwargs: Any) -> Any:
        if self.proxy_url and urlparse(url).netloc in PROXIED_API_HOSTS:
            kwargs.setdefault("proxy", self.proxy_url)
        return self.direct.request(method, url, **kwargs)

    def get(self, url: str, **kwargs: Any) -> Any:
        return self.request("GET", url, **kwargs)


async def fetch_json(
    session: UpstreamSessions,
    url: str,
    *,
    params: dict[str, str] | None = None,
    json_payload: dict[str, Any] | None = None,
    force_direct: bool = False,
) -> Any:
    """GET/POST JSON with per-host throttling and one short retry for transient failures."""
    throttle = THROTTLES.get(urlparse(url).netloc)
    headers = {"User-Agent": USER_AGENT}
    method = "POST" if json_payload is not None else "GET"
    request_options: dict[str, Any] = {"proxy": None} if force_direct else {}
    for attempt in (0, 1):
        if throttle:
            await throttle.wait()
        try:
            async with session.request(
                method,
                url,
                params=params,
                json=json_payload,
                headers=headers,
                timeout=ClientTimeout(total=UPSTREAM_REQUEST_TIMEOUT),
                **request_options,
            ) as resp:
                if attempt == 0 and (resp.status == 429 or 500 <= resp.status < 600):
                    if resp.status == 429:
                        try:
                            delay = float(resp.headers.get("Retry-After") or 1.0)
                        except ValueError:
                            delay = 1.0
                    else:
                        delay = 0.4
                    logging.warning(
                        "transient upstream status %s from %s; retrying", resp.status, url
                    )
                    await asyncio.sleep(min(delay, 3.0))
                    continue
                resp.raise_for_status()
                return await resp.json()
        except ClientResponseError:
            raise
        except (ClientError, TimeoutError) as exc:
            if attempt:
                raise
            logging.warning("upstream request to %s failed (%s); retrying", url, exc)
            await asyncio.sleep(0.4)
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


async def search_shikimori(session: UpstreamSessions, query: str) -> list[Anime]:
    params = {"search": query, "limit": "10", "order": "popularity"}
    data = await fetch_json(session, f"{SHIKIMORI_ORIGIN}/api/animes", params=params)
    return [Anime.from_shikimori(item) for item in data]


async def search_anime(
    session: UpstreamSessions, cache: TTLCache[list[Anime]], query: str
) -> list[Anime]:
    """Search Shikimori and prefer AniList artwork when available."""
    key = query.casefold()
    cached = cache.get(key)
    if cached is not None:
        return cached

    animes = await search_shikimori(session, query)
    if animes:
        animes = await prefer_anilist_covers(session, animes)
    cache.put(key, animes)
    return animes


async def fetch_shikimori_genres(session: UpstreamSessions, anime_id: int) -> list[str]:
    data = await fetch_json(session, f"{SHIKIMORI_ORIGIN}/api/animes/{anime_id}")
    genres = [
        str(genre.get("russian") or genre.get("name") or "") for genre in data.get("genres") or []
    ]
    return [genre for genre in genres if genre][:4]


async def trending_anime(session: UpstreamSessions, cache: TTLCache[list[Anime]]) -> list[Anime]:
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
        logging.warning("Shikimori trending failed", exc_info=True)

    if animes:
        animes = await prefer_anilist_covers(session, animes)
        cache.put("__trending__", animes)
    return animes


@dataclass(frozen=True, slots=True)
class PosterProviderResult:
    posters: list[tuple[str, str]]
    incomplete: bool = False


async def fetch_tenrai_json(session: UpstreamSessions, url: str) -> Any:
    """Try the configured proxy first and use the direct route as a fallback."""
    try:
        return await fetch_json(session, url)
    except Exception:
        if not session.proxy_url:
            raise
        logging.warning("Tenrai proxy request failed for %s; trying direct route", url)
        return await fetch_json(session, url, force_direct=True)


async def fetch_tenrai_pictures(
    session: UpstreamSessions, anime_id: int
) -> PosterProviderResult:
    details = await fetch_tenrai_json(session, f"{TENRAI_API}/anime/{anime_id}")
    images = as_mapping(as_mapping(details).get("data")).get("images")
    jpg = as_mapping(as_mapping(images).get("jpg"))
    primary_url = as_text(jpg.get("large_image_url") or jpg.get("image_url"))
    primary_thumb = as_text(jpg.get("image_url") or jpg.get("large_image_url"))
    posters = [(primary_url, primary_thumb or primary_url)] if primary_url else []
    seen = {primary_url} if primary_url else set()
    try:
        data = await asyncio.wait_for(
            fetch_tenrai_json(session, f"{TENRAI_API}/anime/{anime_id}/pictures"),
            timeout=TENRAI_GALLERY_TIMEOUT,
        )
    except Exception:
        logging.warning("Tenrai gallery is unavailable for %s", anime_id, exc_info=True)
        return PosterProviderResult(posters=posters, incomplete=True)

    for item in as_mapping(data).get("data") or []:
        jpg = as_mapping(as_mapping(item).get("jpg"))
        url = as_text(jpg.get("large_image_url") or jpg.get("image_url"))
        thumb = as_text(jpg.get("image_url") or jpg.get("large_image_url"))
        if url and url not in seen:
            seen.add(url)
            posters.append((url, thumb or url))
    return PosterProviderResult(posters=posters)


async def fetch_shikimori_poster(
    session: UpstreamSessions, anime_id: int
) -> list[tuple[str, str]]:
    """Keep the source artwork available when AniList becomes the preferred cover."""
    query = (
        "query($ids:String!){animes(ids:$ids,limit:1)"
        "{poster{originalUrl mainUrl}}}"
    )
    try:
        data = await fetch_json(
            session,
            f"{SHIKIMORI_ORIGIN}/api/graphql",
            json_payload={"query": query, "variables": {"ids": str(anime_id)}},
        )
        animes = as_mapping(data).get("data")
        items = as_mapping(animes).get("animes")
        first = items[0] if isinstance(items, list) and items else {}
        poster = as_mapping(as_mapping(first).get("poster"))
        url = as_text(poster.get("originalUrl") or poster.get("mainUrl"))
        thumb = as_text(poster.get("mainUrl") or poster.get("originalUrl"))
    except Exception:
        logging.warning(
            "Shikimori GraphQL poster fetch failed for %s", anime_id, exc_info=True
        )
        data = await fetch_json(session, f"{SHIKIMORI_ORIGIN}/api/animes/{anime_id}")
        image = as_mapping(as_mapping(data).get("image"))
        url = absolute_url(as_text(image.get("original") or image.get("preview")))
        thumb = absolute_url(as_text(image.get("preview") or image.get("original")))
    return [(url, thumb or url)] if url and allowed_image(url) else []


async def fetch_anilist_covers(
    session: UpstreamSessions, anime_ids: list[int]
) -> dict[int, tuple[str, str]]:
    """Fetch AniList covers in one request, keyed by the source anime id."""
    if not anime_ids:
        return {}

    query = (
        "query($ids:[Int]){Page(perPage:50){media(idMal_in:$ids,type:ANIME)"
        "{idMal coverImage{extraLarge large}}}}"
    )
    payload = {"query": query, "variables": {"ids": anime_ids}}
    data = await fetch_json(session, ANILIST_API, json_payload=payload)
    page = as_mapping(as_mapping(data).get("data")).get("Page")
    media = as_mapping(page).get("media")
    covers: dict[int, tuple[str, str]] = {}
    for item in media if isinstance(media, list) else []:
        item_data = as_mapping(item)
        source_id = as_int(item_data.get("idMal"))
        cover = as_mapping(item_data.get("coverImage"))
        url = as_text(cover.get("extraLarge") or cover.get("large"))
        thumb = as_text(cover.get("large") or cover.get("extraLarge"))
        if source_id is not None and url and allowed_image(url):
            covers[source_id] = (url, thumb if thumb and allowed_image(thumb) else url)
    return covers


def apply_anilist_covers(animes: list[Anime], covers: dict[int, tuple[str, str]]) -> list[Anime]:
    """Use AniList artwork when available and preserve the original provider as fallback."""
    return [
        replace(
            anime,
            image_url=covers[anime.id][0],
            image_preview=covers[anime.id][1],
            image_source="anilist",
        )
        if anime.id in covers
        else anime
        for anime in animes
    ]


async def prefer_anilist_covers(session: UpstreamSessions, animes: list[Anime]) -> list[Anime]:
    if not animes:
        return animes
    try:
        covers = await fetch_anilist_covers(session, [anime.id for anime in animes])
        return apply_anilist_covers(animes, covers)
    except Exception:
        logging.warning("AniList cover fetch failed; using source posters", exc_info=True)
        return animes


async def fetch_anilist_cover(
    session: UpstreamSessions, anime_id: int
) -> list[tuple[str, str]]:
    cover = (await fetch_anilist_covers(session, [anime_id])).get(anime_id)
    return [cover] if cover else []


def allowed_image(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and parsed.netloc in ALLOWED_IMAGE_HOSTS


async def collect_posters(
    session: UpstreamSessions, anime_id: int
) -> tuple[list[dict[str, str]], list[str]]:
    """Alternative posters and non-blocking provider warnings."""
    results = await asyncio.gather(
        fetch_anilist_cover(session, anime_id),
        fetch_shikimori_poster(session, anime_id),
        asyncio.wait_for(
            fetch_tenrai_pictures(session, anime_id), timeout=TENRAI_FETCH_TIMEOUT
        ),
        return_exceptions=True,
    )
    posters: list[dict[str, str]] = []
    warnings: list[str] = []
    seen: set[str] = set()
    for source, result in zip(("anilist", "shikimori", "tenrai"), results, strict=True):
        if isinstance(result, BaseException):
            logging.warning("poster fetch (%s) failed for %s: %s", source, anime_id, result)
            if source == "tenrai":
                warnings.append(source)
            continue
        if isinstance(result, PosterProviderResult):
            if result.incomplete:
                warnings.append(source)
            pairs = result.posters
        else:
            pairs = result
        for url, thumb in pairs:
            if not allowed_image(url) or url in seen:
                continue
            seen.add(url)
            posters.append(
                {"url": url, "thumb": thumb if allowed_image(thumb) else url, "source": source}
            )
    return posters[:12], warnings


def parse_card_query(text: str) -> str | None:
    text = text.strip()
    if not text.startswith("card:"):
        return None
    card_id = text.removeprefix("card:")
    if not card_id or not all(ch.isalnum() or ch in "-_" for ch in card_id):
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
        "image_source": anime.image_source,
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
        return "Rendered with Shikizilla"
    text = f"<b>{html.escape(title)}</b>"
    subtitle = str(meta.get("subtitle") or "").strip()
    if subtitle and subtitle != title:
        text += f"\n{html.escape(subtitle)}"
    return text


def card_markup(meta: dict[str, Any]) -> InlineKeyboardMarkup | None:
    url = str(meta.get("url") or "")
    if not allowed_link(url):
        return None
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="Открыть на Shikimori", url=url)]]
    )


def card_inline_result(
    card_id: str, meta: dict[str, Any], ru: bool = True
) -> InlineQueryResultCachedPhoto:
    return InlineQueryResultCachedPhoto(
        id=f"card-{card_id}",
        photo_file_id=str(meta["file_id"]),
        title=str(meta.get("title") or "Shikizilla Card"),
        description="Готовая карточка из WebApp" if ru else "Card from the WebApp",
        caption=card_caption(meta),
        parse_mode="HTML",
        reply_markup=card_markup(meta),
    )


async def prepare_card_share(
    bot: Bot, init_data: str, card_id: str, meta: dict[str, Any]
) -> str | None:
    """Create a short-lived message for Telegram's native Mini App share dialog."""
    user = webapp_user(init_data)
    user_id = as_int(user.get("id"))
    if user_id is None or not as_text(meta.get("file_id")):
        return None
    try:
        prepared = await bot.save_prepared_inline_message(
            user_id=user_id,
            result=card_inline_result(
                card_id, meta, is_russian(as_text(user.get("language_code")))
            ),
            allow_user_chats=True,
            allow_bot_chats=False,
            allow_group_chats=True,
            allow_channel_chats=True,
        )
        return as_text(prepared.id)
    except Exception:
        # Older Bot API gateways can lack prepared messages. The response still
        # carries the legacy inline query so the client has a usable fallback.
        logging.exception("Failed to prepare native share for card %s", card_id)
        return None


def anime_markup(anime: Anime) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Открыть на Shikimori", url=anime.page_url)]
        ]
    )


def load_card_meta(settings: Settings, card_id: str) -> dict[str, Any]:
    meta_path = settings.rendered_dir / f"{card_id}.json"
    try:
        loaded = json.loads(meta_path.read_text())
        return loaded if isinstance(loaded, dict) else {}
    except OSError, ValueError:
        return {}


def load_rendered_index(settings: Settings) -> dict[str, str]:
    try:
        loaded = json.loads((settings.rendered_dir / ".rendered-index.json").read_text())
    except OSError, ValueError:
        return {}
    if not isinstance(loaded, dict):
        return {}
    return {
        image_hash: card_id
        for image_hash, card_id in loaded.items()
        if isinstance(image_hash, str)
        and len(image_hash) == 64
        and all(char in "0123456789abcdef" for char in image_hash)
        and isinstance(card_id, str)
        and parse_card_query(f"card:{card_id}") is not None
    }


def save_rendered_index(settings: Settings, index: dict[str, str]) -> None:
    path = settings.rendered_dir / ".rendered-index.json"
    temporary_path = path.with_suffix(".tmp")
    temporary_path.write_text(json.dumps(index, sort_keys=True))
    temporary_path.replace(path)


def find_rendered_card(settings: Settings, image_hash: str, index: dict[str, str]) -> str | None:
    card_id = index.get(image_hash)
    if card_id:
        meta = load_card_meta(settings, card_id)
        if meta.get("image_sha256") == image_hash and as_text(meta.get("file_id")):
            return card_id
        index.pop(image_hash, None)

    # Rebuild one missing index entry from metadata after a crash or manual recovery.
    for path in settings.rendered_dir.glob("*.json"):
        card_id = path.stem
        meta = load_card_meta(settings, card_id)
        if meta.get("image_sha256") == image_hash and as_text(meta.get("file_id")):
            index[image_hash] = card_id
            return card_id
    return None


def webapp_url(
    settings: Settings, query_text: str = "", *, inline_user_id: int | None = None
) -> str:
    params = {"v": WEBAPP_ASSET_VERSION}
    if query_text and not parse_card_query(query_text):
        params["q"] = query_text
    if inline_user_id is not None:
        params["inline_session"] = create_inline_session(
            settings.bot_token, inline_user_id
        )
    query = f"?{urlencode(params)}" if params else ""
    return f"{settings.public_base_url.rstrip('/')}/{query}"


def results_button(
    settings: Settings, query_text: str, ru: bool, user_id: int
) -> InlineQueryResultsButton:
    return InlineQueryResultsButton(
        text="🎨 Собрать карточку в WebApp" if ru else "🎨 Build a card in WebApp",
        web_app=WebAppInfo(
            url=webapp_url(settings, query_text, inline_user_id=user_id)
        ),
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
        # Keep metadata: it contains Telegram's durable file_id, independent of local JPEG LRU.
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
    settings: Settings, session: UpstreamSessions, cache: TTLCache[list[Anime]]
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

        async def answer(results: list[InlineQueryResultUnion], cache_time: int) -> None:
            try:
                await query.answer(
                    results,
                    cache_time=cache_time,
                    is_personal=True,
                    button=results_button(settings, text, ru, query.from_user.id),
                )
            except TelegramBadRequest as exc:
                if "query is too old" not in str(exc).casefold():
                    raise
                logging.info("Inline query %s expired before it could be answered", query.id)

        if card_id:
            meta = load_card_meta(settings, card_id)
            file_id = as_text(meta.get("file_id"))
            if not file_id:
                logging.warning("Inline card %s has no Telegram file_id", card_id)
                await answer([], cache_time=1)
                return
            await answer(
                [card_inline_result(card_id, meta, ru)],
                cache_time=86_400,
            )
            return

        if len(text) < 2:
            await answer([], cache_time=1)
            return

        try:
            async with asyncio.timeout(INLINE_SEARCH_TIMEOUT):
                animes = await search_anime(session, cache, text)
        except Exception as exc:
            logging.warning("anime search failed: %s", exc)
            await answer([], cache_time=1)
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

        await answer(results, cache_time=30)

    return router


async def create_web_app(
    settings: Settings, session: UpstreamSessions, cache: TTLCache[list[Anime]], bot: Bot
) -> web.Application:
    settings.rendered_dir.mkdir(parents=True, exist_ok=True)
    app = web.Application(client_max_size=8 * 1024 * 1024)
    webapp_html = WEBAPP_HTML_PATH.read_text().replace(
        "{{ASSET_VERSION}}", WEBAPP_ASSET_VERSION
    )
    poster_cache: TTLCache[dict[str, Any]] = TTLCache(ttl=3600)
    trending_cache: TTLCache[list[Anime]] = TTLCache(ttl=1800)
    upload_limiter = SlidingWindowRateLimiter(settings.rendered_uploads_per_hour, 3600)
    rendered_lock = asyncio.Lock()

    async def webapp_page(_: web.Request) -> web.Response:
        return web.Response(
            text=webapp_html,
            content_type="text/html",
            headers={"Cache-Control": "no-store"},
        )

    def require_webapp(request: web.Request) -> tuple[str, str, bool]:
        inline_user_id = validate_inline_session(
            request.headers.get("X-Inline-Session", ""),
            settings.bot_token,
            settings.webapp_auth_max_age,
        )
        if inline_user_id is not None:
            return f"user:{inline_user_id}", "", True
        init_data = request.headers.get("X-Telegram-Init-Data", "")
        if not validate_webapp_init_data(
            init_data, settings.bot_token, settings.webapp_auth_max_age
        ):
            raise web.HTTPUnauthorized(text="valid Telegram WebApp session required")
        return webapp_actor(init_data), init_data, False

    async def healthz(_: web.Request) -> web.Response:
        return web.json_response({"ok": True})

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

    async def trending_api(request: web.Request) -> web.Response:
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
            return web.json_response(cached)
        posters, warnings = await collect_posters(session, int(raw_id))
        payload = {"posters": posters, "warnings": warnings}
        if not warnings:
            poster_cache.put(raw_id, payload)
        return web.json_response(payload)

    async def image_proxy(request: web.Request) -> web.Response:
        url = request.query.get("url") or ""
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc not in ALLOWED_IMAGE_HOSTS:
            return web.Response(status=400, text="image host not allowed")
        request_options: dict[str, Any] = {
            "headers": {"User-Agent": USER_AGENT},
            "allow_redirects": False,
        }
        if parsed.netloc in PROXIED_IMAGE_HOSTS and settings.proxy_url:
            request_options["proxy"] = settings.proxy_url
        async with session.get(url, **request_options) as resp:
            resp.raise_for_status()
            try:
                content_length = int(resp.headers.get("Content-Length") or 0)
            except ValueError:
                content_length = 0
            if content_length > MAX_PROXY_IMAGE_BYTES:
                return web.Response(status=413, text="image too large")
            chunks: list[bytes] = []
            total = 0
            async for chunk in resp.content.iter_chunked(64 * 1024):
                total += len(chunk)
                if total > MAX_PROXY_IMAGE_BYTES:
                    return web.Response(status=413, text="image too large")
                chunks.append(chunk)
            body = b"".join(chunks)
            content_type = resp.headers.get("Content-Type", "image/jpeg")
        return web.Response(
            body=body,
            headers={
                "Content-Type": content_type,
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "public, max-age=86400, immutable",
            },
        )

    async def save_rendered(request: web.Request) -> web.Response:
        actor, init_data, inline_mode = require_webapp(request)
        if not upload_limiter.allow(actor):
            return web.json_response(
                {"ok": False, "error": "upload rate limit exceeded"}, status=429
            )
        try:
            payload = await request.json()
        except json.JSONDecodeError, web.HTTPBadRequest:
            return web.json_response({"ok": False, "error": "JSON body required"}, status=400)
        if not isinstance(payload, dict):
            return web.json_response({"ok": False, "error": "JSON object required"}, status=400)
        data_url = str(payload.get("image") or "")
        if not data_url.startswith("data:image/jpeg;base64,"):
            return web.json_response({"ok": False, "error": "jpeg data url required"}, status=400)
        try:
            raw = base64.b64decode(data_url.split(",", 1)[1], validate=True)
        except ValueError:
            return web.json_response({"ok": False, "error": "bad base64"}, status=400)
        if len(raw) > MAX_RENDERED_IMAGE_BYTES:
            return web.json_response({"ok": False, "error": "image too large"}, status=400)
        if not raw.startswith(b"\xff\xd8\xff"):
            return web.json_response(
                {"ok": False, "error": "jpeg magic bytes required"}, status=400
            )

        image_hash = hashlib.sha256(raw).hexdigest()
        async with rendered_lock:
            index = load_rendered_index(settings)
            existing_card_id = find_rendered_card(settings, image_hash, index)
            if existing_card_id:
                save_rendered_index(settings, index)
                logging.info(
                    "Reused rendered card %s for image hash %s", existing_card_id, image_hash
                )
                card_id = existing_card_id
                meta = load_card_meta(settings, card_id)
            else:
                meta_raw = as_mapping(payload.get("meta"))
                url = str(meta_raw.get("url") or "")
                meta = {
                    "title": str(meta_raw.get("title") or "")[:200],
                    "subtitle": str(meta_raw.get("subtitle") or "")[:200],
                    "url": url if allowed_link(url) else "",
                    "image_sha256": image_hash,
                }
                card_id = uuid4().hex
                try:
                    message = await bot.send_photo(
                        chat_id=settings.storage_chat_id,
                        photo=BufferedInputFile(raw, filename=f"shikizilla-{card_id}.jpg"),
                    )
                    if not message.photo:
                        raise RuntimeError("Telegram did not return a photo file_id")
                    meta["file_id"] = message.photo[-1].file_id
                except Exception:
                    logging.exception(
                        "Failed to upload rendered card %s to Telegram storage", card_id
                    )
                    return web.json_response(
                        {"ok": False, "error": "Telegram upload failed"}, status=502
                    )

                try:
                    image_path = settings.rendered_dir / f"{card_id}.jpg"
                    image_path.write_bytes(raw)
                    (settings.rendered_dir / f"{card_id}.json").write_text(
                        json.dumps(meta, ensure_ascii=False)
                    )
                    index[image_hash] = card_id
                    save_rendered_index(settings, index)
                except OSError:
                    logging.exception("Failed to persist rendered card %s", card_id)
                    return web.json_response(
                        {"ok": False, "error": "card storage failed"}, status=500
                    )
                cleanup_rendered_dir(settings)
                logging.info(
                    "Saved rendered card %s (%d bytes) to Telegram storage", card_id, len(raw)
                )

        prepared_message_id = (
            None
            if inline_mode
            else await prepare_card_share(bot, init_data, card_id, meta)
        )
        return web.json_response(
            {
                "ok": True,
                "id": card_id,
                "query": f"card:{card_id}",
                "prepared_message_id": prepared_message_id,
            }
        )

    async def rendered_jpeg(request: web.Request) -> web.StreamResponse:
        card_id = request.match_info["card_id"]
        image_path = settings.rendered_dir / f"{card_id}.jpg"
        if not image_path.is_file():
            raise web.HTTPNotFound()
        return web.FileResponse(
            image_path, headers={"Cache-Control": "public, max-age=31536000, immutable"}
        )

    app.router.add_get("/healthz", healthz)
    app.router.add_get("/", webapp_page)
    app.router.add_static("/static/", WEBAPP_STATIC_DIR, name="static")
    app.router.add_get("/api/search", search_api)
    app.router.add_get("/api/trending", trending_api)
    app.router.add_get("/api/anime/{anime_id}/genres", genres_api)
    app.router.add_get("/api/anime/{anime_id}/posters", posters_api)
    app.router.add_get("/api/image", image_proxy)
    app.router.add_post("/api/rendered", save_rendered)
    app.router.add_get("/rendered/{card_id:[A-Za-z0-9-]+}.jpg", rendered_jpeg)
    return app


async def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    settings = Settings()  # type: ignore[call-arg]  # fields come from env/.env
    cleanup_rendered_dir(settings)
    cache: TTLCache[list[Anime]] = TTLCache(ttl=settings.search_cache_ttl)

    # The VPS has unreliable IPv6 reachability to Cloudflare-backed poster CDNs.
    # Force IPv4 for provider traffic; Telegram continues to use Clash separately.
    async with ClientSession(
        timeout=ClientTimeout(total=15),
        connector=TCPConnector(family=socket.AF_INET, ttl_dns_cache=300),
    ) as direct_session:
        upstream = UpstreamSessions(direct_session, settings.proxy_url)
        bot_session = AiohttpSession(proxy=settings.proxy_url) if settings.proxy_url else None
        bot = Bot(settings.bot_token, session=bot_session)
        dp = Dispatcher()
        dp.include_router(build_router(settings, upstream, cache))

        try:
            await bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(
                    text="Карточки", web_app=WebAppInfo(url=webapp_url(settings))
                )
            )
        except Exception:
            logging.warning("failed to set chat menu button", exc_info=True)

        web_app = await create_web_app(settings, upstream, cache, bot)
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
