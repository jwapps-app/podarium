"""An open page can ask which web build the server is handing out."""

import httpx

from podarium.main import app, web_bundle_name


def test_the_bundle_is_read_from_the_index(tmp_path):
    index = tmp_path / "index.html"
    index.write_text('<script type="module" crossorigin src="/assets/index-DI5siKVx.js"></script>')
    assert web_bundle_name(index) == "index-DI5siKVx.js"


def test_no_index_is_no_answer(tmp_path):
    assert web_bundle_name(tmp_path / "missing.html") is None
    (tmp_path / "index.html").write_text("<html></html>")
    assert web_bundle_name(tmp_path / "index.html") is None


async def test_it_needs_no_sign_in_and_says_only_the_name():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/api/version")
    assert response.status_code == 200
    assert set(response.json()) == {"web"}
