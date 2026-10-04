from fastapi import FastAPI, Response
from fastapi.testclient import TestClient

from api.main import app
from api.middleware import SecurityHeadersMiddleware


# ----------------------------------------------------------------------------
# Security headers — real app (dev environment)
# ----------------------------------------------------------------------------


def test_security_headers_present_on_every_response():
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "no-referrer"


def test_hsts_absent_in_dev_environment():
    # settings.app_env is "development" in the test suite: HSTS must be off,
    # otherwise localhost HTTP dev servers on this host would break.
    client = TestClient(app)

    response = client.get("/health")

    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_present_on_error_responses():
    client = TestClient(app)

    response = client.get("/does-not-exist")

    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# ----------------------------------------------------------------------------
# Security headers — isolated app (production/HSTS + no-clobber behavior)
# ----------------------------------------------------------------------------


def test_hsts_sent_when_production_flag_enabled():
    prod_like = FastAPI()

    @prod_like.get("/ping")
    async def ping():
        return {"ok": True}

    prod_like.add_middleware(SecurityHeadersMiddleware, include_hsts=True)
    client = TestClient(prod_like)

    response = client.get("/ping")

    assert response.headers["Strict-Transport-Security"] == "max-age=63072000; includeSubDomains"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_route_set_headers_are_not_clobbered():
    custom = FastAPI()

    @custom.get("/framable")
    async def framable(response: Response):
        # A route that deliberately allows same-origin framing must win.
        response.headers["X-Frame-Options"] = "SAMEORIGIN"
        return {"ok": True}

    custom.add_middleware(SecurityHeadersMiddleware)
    client = TestClient(custom)

    response = client.get("/framable")

    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    # Untouched headers are still added.
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# ----------------------------------------------------------------------------
# Unhandled errors still reach the browser as a readable 500
# ----------------------------------------------------------------------------


def test_an_unhandled_error_is_a_json_500_that_carries_cors_headers():
    """Without it the 500 had no CORS header and the app reported a network failure."""
    from fastapi.middleware.cors import CORSMiddleware

    from api.middleware import UnhandledErrorMiddleware

    broken = FastAPI()

    @broken.get("/boom")
    def boom():
        raise OSError("APIFY_TOKEN missing")

    broken.add_middleware(UnhandledErrorMiddleware)
    broken.add_middleware(CORSMiddleware, allow_origins=["http://127.0.0.1:4200"], allow_credentials=True)
    client = TestClient(broken, raise_server_exceptions=False)

    response = client.get("/boom", headers={"Origin": "http://127.0.0.1:4200"})

    assert response.status_code == 500
    assert response.json() == {"detail": "Internal Server Error"}
    assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:4200"


def test_the_real_app_installs_the_unhandled_error_middleware_inside_cors():
    from fastapi.middleware.cors import CORSMiddleware

    from api.middleware import UnhandledErrorMiddleware

    order = [entry.cls for entry in app.user_middleware]
    # user_middleware lists the OUTERMOST first.
    assert order.index(CORSMiddleware) < order.index(UnhandledErrorMiddleware)
