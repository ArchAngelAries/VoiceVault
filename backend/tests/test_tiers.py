"""Tier list boards and the image warehouse (api/tiers.py, core/tier_images.py). Isolated DB and image folder."""
import io

import httpx
import pytest
from PIL import Image

from core import tier_images


@pytest.fixture
def image_dir(monkeypatch, tmp_path):
    folder = tmp_path / "tier_images"
    monkeypatch.setattr(tier_images, "IMAGE_DIR", folder)
    return folder


def _png(width=40, height=30, color="red") -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, format="PNG")
    return buf.getvalue()


def _anime(i: int) -> dict:
    return {"id": f"anime:anilist:{100 + i}", "title": f"Show {i}", "item_type": "anime", "source": "anilist",
            "cover_url": f"https://img.example/{i}.jpg"}


async def _new_board(api, name="Anime 2026", **extra) -> dict:
    r = await api.post("/api/tiers", json={"name": name, **extra})
    assert r.status_code == 200, r.text
    return r.json()


async def _favorites_id(api) -> int:
    cols = (await api.get("/api/collections")).json()
    return next(c["id"] for c in cols if c["slug"] == "favorites")


async def _save(api, tier_id, rows, notes=None):
    body = {"rows": rows}
    if notes is not None:
        body["notes"] = notes
    return await api.put(f"/api/tiers/{tier_id}/board", json=body)


# ── Boards ───────────────────────────────────────────────────────

async def test_a_new_board_starts_from_the_sabc_template_on_favorites(api):
    board = await _new_board(api)

    assert [(row["label"], row["color"]) for row in board["rows"]] == [
        ("S", "#ff7f7f"), ("A", "#ffbf7f"), ("B", "#ffdf7f"), ("C", "#ffff7f")]
    assert all(row["items"] == [] for row in board["rows"])
    assert board["template"] == "SABC"
    assert board["collection"]["name"] == "Favorites"


async def test_rows_and_tiles_are_stored_in_order_with_their_snapshots(api):
    board = await _new_board(api)

    r = await _save(api, board["id"], [
        {"label": "S", "color": "#ff7f7f", "items": [_anime(1), _anime(2)]},
        {"label": "A", "color": "#ffbf7f", "items": [_anime(3)]},
        {"label": "Trash", "color": "#7fbfff", "items": []},
    ], notes="Ranked in March.")

    assert r.status_code == 200
    detail = r.json()
    assert [row["label"] for row in detail["rows"]] == ["S", "A", "Trash"]
    assert [tile["title"] for tile in detail["rows"][0]["items"]] == ["Show 1", "Show 2"]
    assert detail["rows"][0]["items"][0]["cover_url"] == "https://img.example/1.jpg"
    assert detail["rows"][0]["items"][0]["kind"] == "item"
    assert detail["notes"] == "Ranked in March."
    again = (await api.get(f"/api/tiers/{board['id']}")).json()
    assert again["rows"] == detail["rows"]


async def test_an_item_cannot_be_placed_in_two_places(api):
    board = await _new_board(api)

    r = await _save(api, board["id"], [
        {"label": "S", "color": "#ff7f7f", "items": [_anime(1)]},
        {"label": "A", "color": "#ffbf7f", "items": [_anime(1)]},
    ])

    assert r.status_code == 422
    assert "more than one place" in r.json()["detail"]


@pytest.mark.parametrize("rows", [
    [{"label": "S", "color": "#ff7f7f", "items": [{"id": "not-a-typed-id"}]}],
    [{"label": "S", "color": "red", "items": []}],
    [{"label": "", "color": "#ff7f7f", "items": []}],
    [{"label": "S", "color": "#ff7f7f", "items": [{"id": "anime:anilist:1", "cover_url": "javascript:alert(1)"}]}],
    [{"label": "S", "color": "#ff7f7f", "items": [{"id": "image:local:" + "0" * 32}]}],
    [{"label": f"R{i}", "color": "#ff7f7f", "items": []} for i in range(31)],
])
async def test_bad_rows_tiles_and_colours_are_refused(api, rows):
    board = await _new_board(api)

    r = await _save(api, board["id"], rows)

    assert r.status_code == 422


async def test_saving_a_missing_board_is_a_404(api):
    r = await _save(api, 999999, [])

    assert r.status_code == 404


async def test_rename_notes_and_detach_then_attach_a_collection(api):
    board = await _new_board(api)

    renamed = await api.patch(f"/api/tiers/{board['id']}", json={"name": "Anime, spring", "notes": "Note."})
    detached = await api.patch(f"/api/tiers/{board['id']}", json={"collection_id": None})
    missing = await api.patch(f"/api/tiers/{board['id']}", json={"collection_id": 999999})
    fav = await _favorites_id(api)
    attached = await api.patch(f"/api/tiers/{board['id']}", json={"collection_id": fav})

    assert renamed.json()["name"] == "Anime, spring" and renamed.json()["notes"] == "Note."
    assert detached.json()["collection"] is None
    assert missing.status_code == 422
    assert attached.json()["collection"]["id"] == fav


async def test_a_blank_name_is_refused(api):
    board = await _new_board(api)

    r = await api.patch(f"/api/tiers/{board['id']}", json={"name": "   "})

    assert r.status_code == 422


async def test_the_list_reports_rows_and_tile_counts_and_delete_removes_the_board(api):
    board = await _new_board(api, template="blank")
    await _save(api, board["id"], [
        {"label": "S", "color": "#ff7f7f", "items": [_anime(1), _anime(2)]},
        {"label": "A", "color": "#ffbf7f", "items": []},
    ])

    listed = next(b for b in (await api.get("/api/tiers")).json()["items"] if b["id"] == board["id"])
    deleted = await api.delete(f"/api/tiers/{board['id']}")

    assert listed["row_count"] == 2 and listed["tile_count"] == 2
    assert deleted.status_code == 200
    assert (await api.get(f"/api/tiers/{board['id']}")).status_code == 404
    assert all(b["id"] != board["id"] for b in (await api.get("/api/tiers")).json()["items"])


async def test_a_blank_template_starts_with_no_rows(api):
    board = await _new_board(api, template="blank")

    assert board["rows"] == [] and board["template"] == "custom"


# ── Imported images ──────────────────────────────────────────────

async def test_import_keeps_images_and_reports_files_that_are_not_images(api, image_dir):
    r = await api.post("/api/tiers/images", files=[
        ("files", ("cover.png", _png(), "image/png")),
        ("files", ("notes.txt", b"hello there", "text/plain")),
    ])

    body = r.json()
    assert r.status_code == 200
    assert len(body["imported"]) == 1 and body["imported"][0]["width"] == 40
    assert body["imported"][0]["id"].startswith("image:local:")
    assert body["failed"] == [{"name": "notes.txt", "reason": "Only PNG, JPEG, WebP and GIF images can be imported."}]
    assert len(list(image_dir.glob("*.png"))) == 1


async def test_an_oversized_image_is_refused(api, image_dir):
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (tier_images.MAX_BYTES + 1)

    r = await api.post("/api/tiers/images", files=[("files", ("big.png", big, "image/png"))])

    assert r.json()["failed"][0]["reason"] == "The image is larger than 10 MB."
    assert list(image_dir.glob("*.png")) == []


async def test_an_image_is_served_and_bad_ids_are_refused(api, image_dir):
    imported = (await api.post("/api/tiers/images", files=[("files", ("a.png", _png(), "image/png"))])).json()["imported"][0]
    hex_id = imported["id"].split(":")[-1]

    ok = await api.get(f"/api/tiers/images/{hex_id}/file")

    assert ok.status_code == 200 and ok.headers["content-type"] == "image/png"
    assert (await api.get("/api/tiers/images/not-an-id/file")).status_code == 404
    assert (await api.get(f"/api/tiers/images/{'c' * 32}/file")).status_code == 404


async def test_an_image_label_is_stored_and_shows_on_the_board(api, image_dir):
    imported = (await api.post("/api/tiers/images", files=[("files", ("a.png", _png(), "image/png"))])).json()["imported"][0]
    hex_id = imported["id"].split(":")[-1]
    board = await _new_board(api)
    await api.patch(f"/api/tiers/images/{hex_id}", json={"label": "Dragon sketch"})

    r = await _save(api, board["id"], [{"label": "S", "color": "#ff7f7f", "items": [{"id": imported["id"]}]}])

    tile = r.json()["rows"][0]["items"][0]
    assert tile["kind"] == "image" and tile["title"] == "Dragon sketch"
    assert tile["cover_url"] == f"/api/tiers/images/{hex_id}/file"
    assert (await api.get("/api/tiers/images")).json()["items"][0]["label"] == "Dragon sketch"


async def test_a_tile_pointing_at_a_deleted_image_is_refused(api, image_dir):
    board = await _new_board(api)

    r = await _save(api, board["id"], [{"label": "S", "color": "#ff7f7f",
                                         "items": [{"id": "image:local:" + "d" * 32}]}])

    assert r.status_code == 422


async def test_deleting_an_image_removes_it_from_every_board(api, image_dir):
    imported = (await api.post("/api/tiers/images", files=[("files", ("a.png", _png(), "image/png"))])).json()["imported"][0]
    first, second = await _new_board(api, "First"), await _new_board(api, "Second")
    for board in (first, second):
        await _save(api, board["id"], [{"label": "S", "color": "#ff7f7f", "items": [{"id": imported["id"]}, _anime(9)]}])
    hex_id = imported["id"].split(":")[-1]

    r = await api.delete(f"/api/tiers/images/{hex_id}")

    assert r.status_code == 200 and r.json()["deleted"] == [imported["id"]]
    for board in (first, second):
        items = (await api.get(f"/api/tiers/{board['id']}")).json()["rows"][0]["items"]
        assert [t["id"] for t in items] == [_anime(9)["id"]]
    assert (await api.get("/api/tiers/images")).json()["items"] == []


async def test_delete_all_images_needs_the_typed_phrase_and_leaves_other_files_alone(api, image_dir):
    await api.post("/api/tiers/images", files=[("files", ("a.png", _png(), "image/png")),
                                                ("files", ("b.png", _png(), "image/png"))])
    (image_dir / "notes.txt").write_text("keep", encoding="utf-8")

    refused = await api.post("/api/tiers/images/bulk-delete", json={"all": True, "confirm": "delete all"})
    assert refused.status_code == 400
    assert len(list(image_dir.glob("*.png"))) == 2

    r = await api.post("/api/tiers/images/bulk-delete", json={"all": True, "confirm": "DELETE ALL"})

    assert r.status_code == 200 and len(r.json()["deleted"]) == 2
    assert list(image_dir.glob("*.png")) == []
    assert (image_dir / "notes.txt").is_file()


async def test_bulk_delete_of_selected_images_reports_missing_ones(api, image_dir):
    imported = (await api.post("/api/tiers/images", files=[("files", ("a.png", _png(), "image/png"))])).json()["imported"][0]
    ghost = "image:local:" + "e" * 32

    r = await api.post("/api/tiers/images/bulk-delete", json={"ids": [imported["id"], ghost]})

    assert r.json() == {"deleted": [imported["id"]], "missing": [ghost], "failed": []}


async def test_a_bulk_image_request_with_a_bad_id_deletes_nothing(api, image_dir):
    imported = (await api.post("/api/tiers/images", files=[("files", ("a.png", _png(), "image/png"))])).json()["imported"][0]

    r = await api.post("/api/tiers/images/bulk-delete", json={"ids": [imported["id"], "../etc/passwd"]})

    assert r.status_code == 400
    assert len((await api.get("/api/tiers/images")).json()["items"]) == 1


# ── Cover proxy (used by export only) ────────────────────────────

class _FakeUpstream:
    def __init__(self, status=200, kind="image/jpeg", body=b"\xff\xd8\xff\xe0fake"):
        self.status_code = status
        self.headers = {"content-type": kind}
        self.content = body


def _fake_client(monkeypatch, response, seen):
    class FakeClient:
        def __init__(self, **kwargs):
            seen["kwargs"] = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, url):
            seen["url"] = url
            return response

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)


@pytest.mark.parametrize("url", [
    "https://evil.example/cover.jpg",
    "http://image.tmdb.org/t/p/w500/a.jpg",
    "https://s4.anilist.co.evil.example/cover.jpg",
    "https://image.tmdb.org.evil.example/a.jpg",
])
async def test_the_cover_proxy_refuses_other_hosts_and_plain_http(api, url):
    r = await api.get("/api/tiers/image-proxy", params={"url": url})

    assert r.status_code == 400


async def test_the_cover_proxy_returns_an_allowed_image_without_following_redirects(api, monkeypatch):
    seen = {}
    _fake_client(monkeypatch, _FakeUpstream(), seen)

    r = await api.get("/api/tiers/image-proxy", params={"url": "https://s4.anilist.co/file/anilistcdn/cover.jpg"})

    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8\xff\xe0fake"
    assert seen["url"] == "https://s4.anilist.co/file/anilistcdn/cover.jpg"
    assert seen["kwargs"]["follow_redirects"] is False


async def test_the_cover_proxy_refuses_a_response_that_is_not_an_image(api, monkeypatch):
    _fake_client(monkeypatch, _FakeUpstream(kind="text/html", body=b"<html>"), {})

    r = await api.get("/api/tiers/image-proxy", params={"url": "https://image.tmdb.org/t/p/w500/a.jpg"})

    assert r.status_code == 502
