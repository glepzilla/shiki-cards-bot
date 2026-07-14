import asyncio
import hashlib
import hmac
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer
from app.main import (
    WEBAPP_ASSET_VERSION,
    Anime,
    Settings,
    SlidingWindowRateLimiter,
    TTLCache,
    UpstreamSessions,
    apply_anilist_covers,
    cleanup_rendered_dir,
    collect_posters,
    create_web_app,
    fetch_jikan_pictures,
    parse_card_query,
    validate_webapp_init_data,
    webapp_url,
)


def make_settings(rendered_dir: Path, max_mb: int = 1) -> Settings:
    return Settings(
        bot_token="test-token",
        public_base_url="https://example.test",
        storage_chat_id=-1001234567890,
        rendered_dir=rendered_dir,
        rendered_max_mb=max_mb,
    )


def signed_init_data(token: str, auth_date: int) -> str:
    values = {"auth_date": str(auth_date), "query_id": "query", "user": '{"id":1}'}
    check = "\n".join(f"{key}={values[key]}" for key in sorted(values))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    values["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(values)


def test_parse_card_query() -> None:
    assert parse_card_query(" card:abc-123 ") == "abc-123"
    assert parse_card_query("card:") is None
    assert parse_card_query("card:../../secret") is None
    assert parse_card_query("anime") is None


def test_webapp_url_uses_the_site_root() -> None:
    settings = make_settings(Path(".cache/rendered"))
    assert webapp_url(settings) == f"https://example.test/?v={WEBAPP_ASSET_VERSION}"
    assert webapp_url(settings, "Fullmetal Alchemist") == (
        f"https://example.test/?v={WEBAPP_ASSET_VERSION}&q=Fullmetal+Alchemist"
    )


def test_anime_from_shikimori_handles_dirty_optional_fields() -> None:
    anime = Anime.from_shikimori(
        {
            "id": "17",
            "name": None,
            "russian": 42,
            "kind": 123,
            "score": "0.0",
            "image": "not-an-object",
            "episodes": "12",
            "aired_on": "invalid",
        }
    )
    assert anime.id == 17
    assert anime.name == "Untitled"
    assert anime.russian == "42"
    assert anime.kind == "123"
    assert anime.score is None
    assert anime.episodes == 12
    assert anime.year is None
    assert anime.image_url is None


def test_anime_from_jikan_handles_dirty_nested_data() -> None:
    anime = Anime.from_jikan(
        {
            "mal_id": "42",
            "title": None,
            "type": "TV",
            "images": {"jpg": "invalid"},
            "genres": [{"name": "Action"}, "invalid", {"name": ""}],
            "episodes": "24",
            "year": "2020",
        }
    )
    assert anime.id == 42
    assert anime.name == "Untitled"
    assert anime.kind == "tv"
    assert anime.genres == ("Action",)
    assert anime.episodes == 24
    assert anime.year == 2020
    assert anime.image_url is None


def test_anilist_cover_replaces_the_default_poster() -> None:
    anime = Anime.from_shikimori(
        {
            "id": "17",
            "name": "Original",
            "image": {"original": "/system/animes/original/17.jpg"},
        }
    )
    cover_url = "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/17.jpg"
    cover_thumb = "https://s4.anilist.co/file/anilistcdn/media/anime/cover/medium/17.jpg"

    [with_anilist_cover] = apply_anilist_covers([anime], {17: (cover_url, cover_thumb)})
    assert with_anilist_cover.image_url == cover_url
    assert with_anilist_cover.image_preview == cover_thumb
    assert with_anilist_cover.image_source == "anilist"
    assert apply_anilist_covers([anime], {}) == [anime]


def test_collect_posters_keeps_source_artwork_when_jikan_is_unavailable() -> None:
    anilist = "https://s4.anilist.co/file/anilistcdn/media/anime/cover/large/17.jpg"
    shikimori = "https://shikimori.io/system/animes/original/17.jpg"

    async def check() -> None:
        with (
            patch("app.main.fetch_anilist_cover", AsyncMock(return_value=[(anilist, anilist)])),
            patch(
                "app.main.fetch_shikimori_poster",
                AsyncMock(return_value=[(shikimori, shikimori)]),
            ),
            patch("app.main.fetch_jikan_pictures", AsyncMock(side_effect=TimeoutError)),
        ):
            posters = await collect_posters(object(), 17)  # type: ignore[arg-type]

        assert [poster["source"] for poster in posters] == ["anilist", "shikimori"]
        assert [poster["url"] for poster in posters] == [anilist, shikimori]

    asyncio.run(check())


def test_collect_posters_exposes_jikan_as_its_own_source() -> None:
    async def check() -> None:
        anilist = "https://s4.anilist.co/file/cover.jpg"
        shikimori = "https://shikimori.io/uploads/poster.jpg"
        jikan = "https://cdn.myanimelist.net/images/anime/1/2l.jpg"
        with (
            patch("app.main.fetch_anilist_cover", AsyncMock(return_value=[(anilist, anilist)])),
            patch(
                "app.main.fetch_shikimori_poster",
                AsyncMock(return_value=[(shikimori, shikimori)]),
            ),
            patch("app.main.fetch_jikan_pictures", AsyncMock(return_value=[(jikan, jikan)])),
        ):
            posters = await collect_posters(object(), 17)  # type: ignore[arg-type]

        assert [poster["source"] for poster in posters] == [
            "anilist",
            "shikimori",
            "jikan",
        ]

    asyncio.run(check())


def test_jikan_pictures_falls_back_to_primary_artwork() -> None:
    async def check() -> None:
        url = "https://cdn.myanimelist.net/images/anime/1/2l.jpg"
        thumb = "https://cdn.myanimelist.net/images/anime/1/2.jpg"
        fetch = AsyncMock(
            side_effect=[
                {"data": {"images": {"jpg": {"large_image_url": url, "image_url": thumb}}}},
                TimeoutError,
            ]
        )
        with patch("app.main.fetch_json", fetch):
            pictures = await fetch_jikan_pictures(object(), 17)  # type: ignore[arg-type]

        assert pictures == [(url, thumb)]
        assert fetch.await_count == 2

    asyncio.run(check())


def test_jikan_primary_artwork_falls_back_from_proxy_to_direct() -> None:
    async def check() -> None:
        url = "https://cdn.myanimelist.net/images/anime/1/2l.jpg"
        fetch = AsyncMock(
            side_effect=[
                TimeoutError,
                {"data": {"images": {"jpg": {"large_image_url": url}}}},
                TimeoutError,
            ]
        )
        with patch("app.main.fetch_json", fetch):
            pictures = await fetch_jikan_pictures(object(), 17)  # type: ignore[arg-type]

        assert pictures == [(url, url)]
        assert fetch.await_args_list[1].kwargs == {"force_direct": True}

    asyncio.run(check())


def test_upstream_sessions_proxy_only_jikan_api_requests() -> None:
    class CapturingSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, dict[str, object]]] = []

        def request(self, method: str, url: str, **kwargs: object) -> object:
            self.calls.append((method, url, kwargs))
            return object()

    direct = CapturingSession()
    proxy_url = "http://proxy.test:7890"
    sessions = UpstreamSessions(direct, proxy_url)  # type: ignore[arg-type]

    for url in (
        "https://shikimori.io/api/animes",
        "https://api.jikan.moe/v4/anime",
        "https://graphql.anilist.co",
        "https://s4.anilist.co/file/cover.jpg",
        "https://cdn.myanimelist.net/images/cover.jpg",
    ):
        sessions.request("GET", url)
    assert len(direct.calls) == 5
    assert direct.calls[0][2].get("proxy") is None
    assert direct.calls[1][2].get("proxy") == proxy_url
    assert all(call[2].get("proxy") is None for call in direct.calls[2:])


def test_ttl_cache_expires_and_evicts_oldest() -> None:
    cache: TTLCache[str] = TTLCache(ttl=60, max_entries=2)
    cache.put("first", "1")
    cache.put("second", "2")
    cache.put("third", "3")
    assert cache.get("first") is None
    assert cache.get("second") == "2"
    expired: TTLCache[str] = TTLCache(ttl=-1)
    expired.put("key", "value")
    assert expired.get("key") is None


def test_upload_rate_limiter_rejects_excess_requests() -> None:
    limiter = SlidingWindowRateLimiter(limit=2, window=60)
    assert limiter.allow("user:1")
    assert limiter.allow("user:1")
    assert not limiter.allow("user:1")
    assert limiter.allow("user:2")


def test_cleanup_removes_old_jpegs_but_keeps_file_id_metadata(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, max_mb=1)
    old = tmp_path / "old.jpg"
    new = tmp_path / "new.jpg"
    old.write_bytes(b"x" * 700_000)
    new.write_bytes(b"x" * 700_000)
    old_meta = old.with_suffix(".json")
    old_meta.write_text('{"file_id":"telegram-file-id"}')
    os.utime(old, (time.time() - 100, time.time() - 100))

    cleanup_rendered_dir(settings)

    assert not old.exists()
    assert new.exists()
    assert old_meta.exists()


def test_webapp_rejects_unauthenticated_requests_and_upload_failures(tmp_path: Path) -> None:
    class FailingBot:
        async def send_photo(self, **_: object) -> None:
            raise RuntimeError("Telegram unavailable")

    async def check() -> None:
        settings = make_settings(tmp_path)
        async with ClientSession() as direct:
            upstream = UpstreamSessions(direct)
            app = await create_web_app(settings, upstream, TTLCache(ttl=60), FailingBot())  # type: ignore[arg-type]
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                assert (await client.get("/healthz")).status == 200
                response = await client.get("/api/search?q=x")
                assert response.status == 200
                assert (await client.get("/static/ds/styles.css")).status == 200
                assert (await client.get("/static/webapp.css")).status == 200
                assert (await client.get("/static/webapp.js")).status == 200
                webapp = await client.get("/")
                assert webapp.status == 200
                html = await webapp.text()
                assert webapp.headers["Cache-Control"] == "no-store"
                assert "/static/ds/_ds_bundle.js?v=" in html
                assert "/static/webapp.css?v=" in html
                assert "/static/webapp.js?v=" in html
                assert "{{ASSET_VERSION}}" not in html
                assert (await client.get("/webapp")).status == 404
                assert (await client.get("/rendered/card.json")).status == 404

                assert (await client.post("/api/rendered")).status == 401
            finally:
                await client.close()

    asyncio.run(check())


def test_validate_webapp_init_data_rejects_tampering_and_expiry() -> None:
    now = int(time.time())
    init_data = signed_init_data("test-token", now)
    assert validate_webapp_init_data(init_data, "test-token", 60)
    assert not validate_webapp_init_data(init_data.replace("query", "other"), "test-token", 60)
    assert not validate_webapp_init_data(signed_init_data("test-token", now - 61), "test-token", 60)
