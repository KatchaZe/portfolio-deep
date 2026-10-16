"""With APP_TOKEN set, /static (code only) is served without a token and always
revalidated, while every page and API route stays locked. Without the exemption, a
browser that never stores the secure cookie got an unstyled page that could not even
show its own "open /?token=" message."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _client():
    os.environ["APP_TOKEN"] = "secret"
    for k in ("RENDER", "RENDER_EXTERNAL_URL", "PUBLIC_DEPLOY"):
        os.environ.pop(k, None)
    import app
    importlib.reload(app)
    from fastapi.testclient import TestClient
    return TestClient(app.app, base_url="http://lan.local")


def test_static_open_and_revalidated():
    c = _client()
    for path in ("/static/js/app.js", "/static/dashboard.css"):
        r = c.get(path)
        assert r.status_code == 200, (path, r.status_code)
        assert r.headers.get("cache-control") == "no-cache", (path, r.headers)
        assert "set-cookie" not in r.headers, path


def test_data_routes_stay_locked():
    c = _client()
    for path in ("/", "/api/portfolio", "/api/risk", "/api/assumptions"):
        assert c.get(path).status_code == 401, path
    assert c.get("/?token=secret").status_code == 200


if __name__ == "__main__":
    from tests._harness import run_suite
    run_suite(globals(), "ALL test_static_auth OK")
