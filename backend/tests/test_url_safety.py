"""Ported from recipe-table tests/test_url_safety.py.

Behaviour change: robots.txt no longer blocks user-initiated single fetches. The old
robots fail-closed / path-specific tests now pass respect_robots=True (the opt-in kept
for automated crawls), and new tests assert default fetches ignore robots.txt entirely.
"""

from __future__ import annotations

import socket
from dataclasses import replace

import cookt.url_safety as url_safety
import httpx
import pytest
from cookt.url_safety import (
    FetchError,
    UrlSafetyError,
    fetch_recipe_page,
    is_public_ip,
    normalize_url,
    resolve_public_addresses,
    url_hash,
    validate_resource_target,
)


@pytest.fixture(autouse=True)
def _mock_transport_mode(monkeypatch):
    # MockTransport responses carry no network stream, so the connected-peer check is
    # skipped the same way recipe-table's TEST_MODE did.
    monkeypatch.setattr(url_safety, "settings", replace(url_safety.settings, test_mode=True))


def _mock_client(monkeypatch, handler):
    real_client = httpx.Client

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(url_safety.httpx, "Client", client_factory)
    monkeypatch.setattr(
        url_safety, "resolve_public_addresses", lambda *_args, **_kwargs: {"93.184.216.34"}
    )


def test_url_normalization_upgrades_tls_and_removes_tracking():
    normalized = normalize_url(
        "HTTP://WWW.Example.COM:80//pie?utm_source=newsletter&b=2&a=1#method"
    )
    assert normalized == "https://www.example.com/pie?a=1&b=2"
    assert len(url_hash(normalized)) == 64


def test_resource_validation_preserves_signed_query_order(monkeypatch):
    monkeypatch.setattr(
        "cookt.url_safety.resolve_public_addresses",
        lambda *_args, **_kwargs: {"93.184.216.34"},
    )
    url = "https://cdn.example/photo.jpg?signature=zxy&width=1200#ignored"
    assert validate_resource_target(url) == (
        "https://cdn.example/photo.jpg?signature=zxy&width=1200"
    )


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "https://127.0.0.1/recipe",
        "https://[::1]/recipe",
        "https://user:password@example.com/recipe",
        "https://example.com:8080/recipe",
        "https://youtube.com/watch?v=abc",
    ],
)
def test_unsafe_url_shapes_are_rejected(url: str):
    with pytest.raises(UrlSafetyError):
        normalize_url(url)


def test_ip_classification_rejects_all_non_global_ranges():
    assert is_public_ip("93.184.216.34")
    for value in ["127.0.0.1", "10.0.0.2", "169.254.169.254", "::1", "fc00::1"]:
        assert not is_public_ip(value)


def test_all_dns_results_must_be_public(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.1", 443)),
        ],
    )
    with pytest.raises(UrlSafetyError, match="non-public"):
        resolve_public_addresses("example.test")


def test_redirect_to_private_ip_is_rejected(monkeypatch):
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /",
            )
        return httpx.Response(302, headers={"location": "https://127.0.0.1/admin"})

    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr("cookt.url_safety.httpx.Client", client_factory)
    monkeypatch.setattr(
        "cookt.url_safety.resolve_public_addresses",
        lambda *_args, **_kwargs: {"93.184.216.34"},
    )
    with pytest.raises(UrlSafetyError, match="IP-literal"):
        fetch_recipe_page("https://example.test/recipe")


def test_browser_challenge_is_reported_without_bypass(monkeypatch):
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(
                200,
                headers={"content-type": "text/plain"},
                text="User-agent: *\nAllow: /",
            )
        return httpx.Response(
            403,
            headers={
                "content-type": "text/html",
                "cf-mitigated": "challenge",
            },
            text="<html><title>Just a moment...</title></html>",
        )

    transport = httpx.MockTransport(handler)

    def client_factory(**kwargs):
        kwargs["transport"] = transport
        return real_client(**kwargs)

    monkeypatch.setattr("cookt.url_safety.httpx.Client", client_factory)
    monkeypatch.setattr(
        "cookt.url_safety.resolve_public_addresses",
        lambda *_args, **_kwargs: {"93.184.216.34"},
    )

    with pytest.raises(FetchError) as caught:
        fetch_recipe_page("https://example.test/recipe")

    assert caught.value.code == "browser_challenge"
    assert "browser verification" in str(caught.value)


def test_unreachable_robots_policy_fails_closed(monkeypatch):
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(503, text="Try later")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html />")

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr("cookt.url_safety.httpx.Client", client_factory)
    monkeypatch.setattr(
        "cookt.url_safety.resolve_public_addresses",
        lambda *_args, **_kwargs: {"93.184.216.34"},
    )
    with pytest.raises(FetchError) as caught:
        fetch_recipe_page("https://example.test/recipe", respect_robots=True)
    assert caught.value.code == "robots_denied"


def test_missing_robots_policy_allows_fetch(monkeypatch):
    real_client = httpx.Client

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text="<html><body>Recipe</body></html>",
        )

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr("cookt.url_safety.httpx.Client", client_factory)
    monkeypatch.setattr(
        "cookt.url_safety.resolve_public_addresses",
        lambda *_args, **_kwargs: {"93.184.216.34"},
    )
    result = fetch_recipe_page("https://example.test/recipe", respect_robots=True)
    assert result.source_site == "example.test"


def test_cached_robots_rules_remain_path_specific(monkeypatch):
    real_client = httpx.Client
    robots_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal robots_requests
        if request.url.path == "/robots.txt":
            robots_requests += 1
            return httpx.Response(
                200,
                text="User-agent: *\nAllow: /public\nDisallow: /private",
            )
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html />")

    def client_factory(**kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_client(**kwargs)

    monkeypatch.setattr(url_safety, "settings", replace(url_safety.settings, test_mode=False))
    monkeypatch.setattr(url_safety, "_validate_connected_peer", lambda *_args: None)
    monkeypatch.setattr(url_safety.httpx, "Client", client_factory)
    monkeypatch.setattr(
        url_safety,
        "resolve_public_addresses",
        lambda *_args, **_kwargs: {"93.184.216.34"},
    )
    url_safety._robots_cache.clear()
    assert (
        fetch_recipe_page("https://example.test/public/recipe", respect_robots=True).source_site
        == "example.test"
    )
    with pytest.raises(FetchError) as caught:
        fetch_recipe_page("https://example.test/private/recipe", respect_robots=True)
    assert caught.value.code == "robots_denied"
    assert robots_requests == 1
    url_safety._robots_cache.clear()


def test_user_initiated_fetch_ignores_robots_policy(monkeypatch):
    """New behaviour: a person importing one URL is not blocked by robots.txt."""
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /")
        return httpx.Response(
            200, headers={"content-type": "text/html"}, text="<html><body>Pie</body></html>"
        )

    _mock_client(monkeypatch, handler)
    result = fetch_recipe_page("https://example.test/recipe")
    assert result.source_site == "example.test"
    assert "/robots.txt" not in requested


def test_unreachable_robots_does_not_block_default_fetch(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(503, text="Try later")
        return httpx.Response(200, headers={"content-type": "text/html"}, text="<html />")

    _mock_client(monkeypatch, handler)
    assert fetch_recipe_page("https://example.test/recipe").source_site == "example.test"


def test_redirects_are_revalidated_without_robots(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest"})

    _mock_client(monkeypatch, handler)
    with pytest.raises(UrlSafetyError, match="IP-literal"):
        fetch_recipe_page("https://example.test/recipe")


def test_page_byte_cap_is_enforced(monkeypatch):
    monkeypatch.setattr(
        url_safety,
        "settings",
        replace(url_safety.settings, test_mode=True, fetch_max_bytes=1_000),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text="x" * 5_000)

    _mock_client(monkeypatch, handler)
    with pytest.raises(FetchError) as caught:
        fetch_recipe_page("https://example.test/recipe")
    assert caught.value.code == "page_too_large"


def test_restricted_page_is_rejected_unless_caller_defers(monkeypatch):
    page = (
        "<html><body><form><input type='password'></form><p>Subscribe to continue</p></body></html>"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/html"}, text=page)

    _mock_client(monkeypatch, handler)
    with pytest.raises(FetchError) as caught:
        fetch_recipe_page("https://example.test/recipe")
    assert caught.value.code == "restricted_page"
    deferred = fetch_recipe_page("https://example.test/recipe", allow_restricted=True)
    assert deferred.restricted is True


def test_redirect_follows_to_canonical_same_site(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/old":
            return httpx.Response(301, headers={"location": "/new?utm_source=x"})
        return httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text='<html><head><link rel="canonical" href="https://www.example.test/pie">'
            "</head><body>Pie</body></html>",
        )

    _mock_client(monkeypatch, handler)
    result = fetch_recipe_page("https://www.example.test/old")
    assert result.final_url == "https://www.example.test/new"
    assert result.canonical_url == "https://www.example.test/pie"
