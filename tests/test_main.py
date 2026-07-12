import asyncio
import base64
import hashlib
import hmac
import os
import time
from pathlib import Path
from urllib.parse import urlencode

from aiohttp import ClientSession
from aiohttp.test_utils import TestClient, TestServer
from app.main import (
    Anime,
    Settings,
    SlidingWindowRateLimiter,
    TTLCache,
    cleanup_rendered_dir,
    create_web_app,
    parse_card_query,
    validate_webapp_init_data,
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
        async with ClientSession() as session:
            app = await create_web_app(settings, session, TTLCache(ttl=60), FailingBot())  # type: ignore[arg-type]
            client = TestClient(TestServer(app))
            await client.start_server()
            try:
                response = await client.get("/api/search?q=test")
                assert response.status == 401
                assert (await client.get("/rendered/card.json")).status == 404

                init_data = signed_init_data("test-token", int(time.time()))
                response = await client.post(
                    "/api/rendered",
                    headers={"X-Telegram-Init-Data": init_data},
                    json={
                        "image": "data:image/jpeg;base64,"
                        + base64.b64encode(b"\xff\xd8\xfffake").decode(),
                    },
                )
                assert response.status == 502
            finally:
                await client.close()

    asyncio.run(check())


def test_validate_webapp_init_data_rejects_tampering_and_expiry() -> None:
    now = int(time.time())
    init_data = signed_init_data("test-token", now)
    assert validate_webapp_init_data(init_data, "test-token", 60)
    assert not validate_webapp_init_data(init_data.replace("query", "other"), "test-token", 60)
    assert not validate_webapp_init_data(signed_init_data("test-token", now - 61), "test-token", 60)
