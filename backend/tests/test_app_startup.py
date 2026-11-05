"""The app starts through its lifespan handler and serves the system collections."""
from fastapi.testclient import TestClient


def test_startup_creates_system_collections_and_serves_them(db_file):
    import db.client as dc

    dc._db = None
    from main import app

    with TestClient(app) as client:
        assert client.get("/api/health/").json()["status"] == "ok"
        slugs = [c["slug"] for c in client.get("/api/collections").json()]

    assert slugs == ["favorites", "want-to-grab", "have-clips"]
