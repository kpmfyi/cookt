"""SSRF-safe fetching for recipe pages and images (ported from recipe-table).

Every hop (initial URL, each redirect, canonical link) is re-validated: HTTP(S) only,
standard ports, no credentials, no IP literals, and every DNS answer must be a global
address. Connections are pinned to the validated addresses so a DNS change between
validation and connect cannot reach a private host. Bodies are capped while streaming.

Behaviour change from recipe-table: robots.txt is advisory for user-initiated single
imports. ``fetch_recipe_page`` only consults it when a caller passes
``respect_robots=True`` (for future bulk/automated crawls).
"""

from __future__ import annotations

import hashlib
import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpcore
import httpx
from bs4 import BeautifulSoup
from httpcore._backends.sync import SyncStream

from .config import settings

TRACKING_PARAMETERS = {
    "fbclid",
    "gclid",
    "dclid",
    "mc_cid",
    "mc_eid",
    "ref",
    "ref_src",
    "source",
}
BLOCKED_HOSTS = {
    "facebook.com",
    "instagram.com",
    "tiktok.com",
    "youtube.com",
    "youtu.be",
    "vimeo.com",
    "x.com",
    "twitter.com",
}


class UrlSafetyError(ValueError):
    pass


class FetchError(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class FetchResult:
    html: str
    canonical_url: str
    content_hash: str
    source_site: str
    # True when the page looks paywalled/login-gated. Only returned (instead of raised)
    # when the caller passes ``allow_restricted=True``; the page may still carry a
    # complete Schema.org recipe.
    restricted: bool = False
    final_url: str = ""


@dataclass(frozen=True, slots=True)
class ResourceFetchResult:
    body: bytes
    url: str
    content_type: str


_robots_cache: dict[
    str,
    tuple[float, Literal["allow", "deny", "rules"], RobotFileParser | None],
] = {}
_robots_cache_lock = threading.Lock()
_pinned_destination: ContextVar[tuple[str, int, frozenset[str]] | None] = ContextVar(
    "cookt_pinned_destination", default=None
)


def _hostname(parts) -> str:
    host = (parts.hostname or "").rstrip(".").casefold()
    if not host:
        raise UrlSafetyError("URL must include a host")
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError as exc:
        raise UrlSafetyError("URL host is invalid") from exc


def _is_blocked_host(host: str) -> bool:
    return any(host == item or host.endswith(f".{item}") for item in BLOCKED_HOSTS)


def normalize_url(raw_url: str) -> str:
    try:
        parts = urlsplit(raw_url.strip())
    except ValueError as exc:
        raise UrlSafetyError("URL is invalid") from exc
    if parts.scheme.casefold() not in {"http", "https"}:
        raise UrlSafetyError("Only HTTP(S) recipe pages are supported")
    if parts.username or parts.password:
        raise UrlSafetyError("URLs containing credentials are not supported")
    host = _hostname(parts)
    if _is_blocked_host(host):
        raise UrlSafetyError("Social and video pages are not supported")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UrlSafetyError("IP-literal URLs are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlSafetyError("URL port is invalid") from exc
    if port not in {None, 80, 443}:
        raise UrlSafetyError("Only standard web ports are supported")

    # Inputs using HTTP are accepted, but fetching and persisted canonical URLs use TLS.
    scheme = "https"
    netloc = host
    if port not in {None, 80, 443}:
        netloc = f"{host}:{port}"
    path = parts.path or "/"
    while "//" in path:
        path = path.replace("//", "/")
    parameters = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in TRACKING_PARAMETERS:
            continue
        parameters.append((key, value))
    query = urlencode(sorted(parameters))
    return urlunsplit((scheme, netloc, path, query, ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(url.encode("utf-8")).hexdigest()


def is_public_ip(value: str) -> bool:
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return False
    return address.is_global


def resolve_public_addresses(host: str, port: int = 443) -> set[str]:
    try:
        records = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise UrlSafetyError("Host could not be resolved") from exc
    addresses = {record[4][0] for record in records}
    if not addresses or any(not is_public_ip(address) for address in addresses):
        raise UrlSafetyError("URL resolves to a non-public destination")
    return addresses


class PublicNetworkBackend(httpcore.SyncBackend):
    """Resolve once, then connect a numeric socket before sending TLS or HTTP.

    HTTPcore retains the original origin for Host, SNI, and certificate checks.
    No second hostname lookup occurs between validation and socket.connect().
    """

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        pinned = _pinned_destination.get()
        if pinned is not None:
            if (host, port) != pinned[:2]:
                raise UrlSafetyError("Connection origin differs from its validated destination")
            addresses = pinned[2]
        else:
            addresses = resolve_public_addresses(host, port)
        deadline = None if timeout is None else time.monotonic() + timeout
        last_error = None
        for address in sorted(addresses)[:16]:
            remaining = None if deadline is None else deadline - time.monotonic()
            if remaining is not None and remaining <= 0:
                raise httpcore.ConnectTimeout("Public destination connection timed out")
            sock = socket.socket(
                socket.AF_INET6 if ":" in address else socket.AF_INET, socket.SOCK_STREAM
            )
            try:
                sock.settimeout(remaining)
                for option in socket_options or []:
                    sock.setsockopt(*option)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                if local_address is not None:
                    sock.bind((local_address, 0))
                sock.connect((address, port))
                peer = _normalized_address(sock.getpeername()[0])
                if not is_public_ip(peer) or peer != _normalized_address(address):
                    raise FetchError("peer_changed", "Connected destination was not approved")
                return SyncStream(sock)
            except BaseException as exc:
                sock.close()
                if not isinstance(exc, OSError):
                    raise
                last_error = exc
        if isinstance(last_error, TimeoutError):
            raise httpcore.ConnectTimeout(str(last_error)) from last_error
        raise httpcore.ConnectError(str(last_error)) from last_error


class PublicHTTPTransport(httpx.HTTPTransport):
    def __init__(self):
        # HTTPX's sync adapter delegates to this pool. Keep the dependency range
        # bounded and exercise the adapter through HTTPX in the transport tests.
        self._pool = httpcore.ConnectionPool(
            ssl_context=httpx.create_ssl_context(trust_env=False),
            network_backend=PublicNetworkBackend(),
            max_keepalive_connections=0,
        )

    def handle_request(self, request):
        addresses = request.extensions.get("cookt.public_addresses")
        if addresses is None:
            return super().handle_request(request)
        if not addresses or any(not is_public_ip(address) for address in addresses):
            raise UrlSafetyError("Connection destination must be public")
        token = _pinned_destination.set(
            (
                request.url.host,
                request.url.port or (443 if request.url.scheme == "https" else 80),
                frozenset(addresses),
            )
        )
        try:
            return super().handle_request(request)
        finally:
            _pinned_destination.reset(token)


def validate_target(url: str) -> str:
    normalized = normalize_url(url)
    parts = urlsplit(normalized)
    resolve_public_addresses(_hostname(parts), parts.port or 443)
    return normalized


def validate_resource_target(raw_url: str) -> str:
    """Validate a discovered public resource URL without reordering signed queries."""

    try:
        parts = urlsplit(raw_url.strip())
    except ValueError as exc:
        raise UrlSafetyError("Resource URL is invalid") from exc
    if parts.scheme.casefold() not in {"http", "https"}:
        raise UrlSafetyError("Resource URL must use HTTP(S)")
    if parts.username or parts.password:
        raise UrlSafetyError("Resource URLs containing credentials are not supported")
    host = _hostname(parts)
    if _is_blocked_host(host):
        raise UrlSafetyError("Social and video resources are not supported")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise UrlSafetyError("IP-literal resource URLs are not allowed")
    try:
        port = parts.port
    except ValueError as exc:
        raise UrlSafetyError("Resource URL port is invalid") from exc
    if port not in {None, 80, 443}:
        raise UrlSafetyError("Only standard web ports are supported")
    resolve_public_addresses(host, port or (443 if parts.scheme.casefold() == "https" else 80))
    netloc = host if port is None else f"{host}:{port}"
    return urlunsplit((parts.scheme.casefold(), netloc, parts.path or "/", parts.query, ""))


def _same_site(first_url: str, second_url: str) -> bool:
    first = _hostname(urlsplit(first_url)).removeprefix("www.")
    second = _hostname(urlsplit(second_url)).removeprefix("www.")
    return first == second


def _looks_restricted(soup: BeautifulSoup) -> bool:
    if soup.select_one('input[type="password"]'):
        return True
    visible = " ".join(soup.stripped_strings).casefold()[:20_000]
    indicators = {
        "sign in to continue",
        "log in to continue",
        "subscribe to continue",
        "this content is for subscribers",
        "already a subscriber? sign in",
    }
    return any(indicator in visible for indicator in indicators)


def _normalized_address(value: str) -> str:
    address = ipaddress.ip_address(value)
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
        return str(address.ipv4_mapped)
    return str(address)


def _validate_connected_peer(response: httpx.Response, expected: set[str]) -> None:
    """Reject a connection when DNS changed to an unapproved peer after validation."""

    if settings.test_mode:
        return
    stream = response.extensions.get("network_stream")
    peer = stream.get_extra_info("server_addr") if stream is not None else None
    if isinstance(peer, tuple) and peer:
        peer = peer[0]
    if not isinstance(peer, str):
        raise FetchError("peer_unverified", "The remote server address could not be verified")
    try:
        normalized_peer = _normalized_address(peer)
        normalized_expected = {_normalized_address(value) for value in expected}
    except ValueError as exc:
        raise FetchError("peer_unverified", "The remote server address was invalid") from exc
    if not is_public_ip(normalized_peer) or normalized_peer not in normalized_expected:
        raise FetchError("peer_changed", "The remote server address changed during connection")


def _request_with_limits(
    client: httpx.Client,
    url: str,
    expected_addresses: set[str],
) -> tuple[httpx.Response, bytes]:
    try:
        with client.stream(
            "GET", url, extensions={"cookt.public_addresses": expected_addresses}
        ) as response:
            _validate_connected_peer(response, expected_addresses)
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > settings.fetch_max_bytes:
                    raise FetchError("page_too_large", "Recipe page exceeded the download limit")
                chunks.append(chunk)
            return response, b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise FetchError("fetch_timeout", "Recipe page timed out") from exc
    except httpx.HTTPError as exc:
        raise FetchError("fetch_failed", "Recipe page could not be fetched") from exc


def _request_resource_with_limit(
    client: httpx.Client,
    url: str,
    max_bytes: int,
    expected_addresses: set[str],
    resource_kind: Literal["image", "feed"] = "image",
) -> tuple[httpx.Response, bytes]:
    label = "Recipe image" if resource_kind == "image" else "Recipe feed"
    code_prefix = "image" if resource_kind == "image" else "feed"
    try:
        with client.stream(
            "GET", url, extensions={"cookt.public_addresses": expected_addresses}
        ) as response:
            _validate_connected_peer(response, expected_addresses)
            chunks: list[bytes] = []
            size = 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > max_bytes:
                    raise FetchError(
                        f"{code_prefix}_too_large",
                        f"{label} exceeded the download limit",
                    )
                chunks.append(chunk)
            return response, b"".join(chunks)
    except httpx.TimeoutException as exc:
        raise FetchError(f"{code_prefix}_fetch_timeout", f"{label} timed out") from exc
    except httpx.HTTPError as exc:
        raise FetchError(
            f"{code_prefix}_fetch_failed",
            f"{label} could not be fetched",
        ) from exc


def _robots_allowed(
    client: httpx.Client,
    url: str,
    before_request: Callable[[str], None] | None = None,
) -> bool:
    parts = urlsplit(url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    if not settings.test_mode:
        with _robots_cache_lock:
            cached = _robots_cache.get(origin)
        if cached and cached[0] > time.monotonic():
            if cached[1] == "allow":
                return True
            if cached[1] == "deny":
                return False
            return bool(cached[2] and cached[2].can_fetch(settings.fetch_user_agent, url))
    allowed = False
    policy_status: Literal["allow", "deny", "rules"] = "deny"
    policy_parser: RobotFileParser | None = None
    try:
        current = validate_target(robots_url)
        for _ in range(settings.fetch_max_redirects + 1):
            current_parts = urlsplit(current)
            expected = resolve_public_addresses(_hostname(current_parts), current_parts.port or 443)
            if before_request:
                before_request(current)
            response, body = _request_with_limits(client, current, expected)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    break
                target = normalize_url(urljoin(current, location))
                if not _same_site(robots_url, target):
                    break
                current = validate_target(target)
                continue
            if 400 <= response.status_code < 500:
                allowed = True
                policy_status = "allow"
                break
            if response.status_code >= 500:
                break
            parser = RobotFileParser()
            parser.set_url(current)
            parser.parse(body.decode(response.encoding or "utf-8", errors="replace").splitlines())
            allowed = parser.can_fetch(settings.fetch_user_agent, url)
            policy_status = "rules"
            policy_parser = parser
            break
    except (FetchError, UrlSafetyError):
        # RFC 9309 treats network and 5xx failures as temporarily unreachable.
        allowed = False
    if not settings.test_mode and settings.robots_cache_seconds > 0:
        with _robots_cache_lock:
            _robots_cache[origin] = (
                time.monotonic() + settings.robots_cache_seconds,
                policy_status,
                policy_parser,
            )
    return allowed


def _is_browser_challenge(response: httpx.Response) -> bool:
    """Identify explicit anti-bot challenges without trying to bypass them."""
    return response.headers.get("cf-mitigated", "").casefold() == "challenge"


def fetch_recipe_page(
    url: str,
    *,
    before_request: Callable[[str], None] | None = None,
    respect_robots: bool = False,
    allow_restricted: bool = False,
) -> FetchResult:
    """Fetch one recipe page.

    ``respect_robots`` defaults to False: a person pasting a URL is a user-initiated
    single fetch, like a browser visit. SSRF protections, redirect re-validation and
    byte caps always apply.
    """
    current = validate_target(url)
    headers = {
        "User-Agent": settings.fetch_user_agent,
        "Accept": "text/html,application/xhtml+xml;q=0.9",
        "Accept-Language": "en-US,en;q=0.8",
    }
    timeout = httpx.Timeout(settings.fetch_timeout_seconds)
    with httpx.Client(
        transport=PublicHTTPTransport(),
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        robots_checked: set[str] = set()
        for _ in range(settings.fetch_max_redirects + 1):
            current = validate_target(current)
            parts = urlsplit(current)
            origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
            if respect_robots and origin not in robots_checked:
                if not _robots_allowed(client, current, before_request):
                    raise FetchError(
                        "robots_denied",
                        "This site does not permit automated recipe access",
                    )
                robots_checked.add(origin)
            expected = resolve_public_addresses(_hostname(parts), parts.port or 443)
            if before_request:
                before_request(current)
            response, body = _request_with_limits(client, current, expected)
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise FetchError("bad_redirect", "Recipe page returned an empty redirect")
                target = normalize_url(urljoin(current, location))
                validate_target(target)
                current = target
                continue
            if _is_browser_challenge(response):
                raise FetchError(
                    "browser_challenge",
                    "This site requires browser verification and cannot be imported automatically",
                )
            if response.status_code >= 400:
                raise FetchError(
                    "fetch_status",
                    f"Recipe page returned HTTP {response.status_code}",
                )
            content_type = response.headers.get("content-type", "").casefold()
            if "html" not in content_type and "xhtml" not in content_type:
                raise FetchError("not_html", "URL did not return an HTML page")
            html = body.decode(response.encoding or "utf-8", errors="replace")
            canonical = current
            soup = BeautifulSoup(html, "html.parser")
            restricted = _looks_restricted(soup)
            if restricted and not allow_restricted:
                raise FetchError(
                    "restricted_page",
                    "Paywalled and login-required recipe pages are not supported",
                )
            canonical_tag = soup.find("link", rel=lambda value: value and "canonical" in value)
            if canonical_tag and canonical_tag.get("href"):
                candidate = normalize_url(urljoin(current, str(canonical_tag["href"])))
                if _same_site(current, candidate):
                    validate_target(candidate)
                    canonical = candidate
            return FetchResult(
                html=html,
                canonical_url=canonical,
                content_hash=hashlib.sha256(body).hexdigest(),
                source_site=_hostname(urlsplit(canonical)).removeprefix("www."),
                restricted=restricted,
                final_url=current,
            )
    raise FetchError("too_many_redirects", "Recipe page redirected too many times")


def fetch_public_resource(url: str, *, max_bytes: int, accept: str) -> ResourceFetchResult:
    """Fetch a bounded public resource with the same SSRF and redirect protections as pages."""

    current = validate_resource_target(url)
    headers = {"User-Agent": settings.fetch_user_agent, "Accept": accept}
    timeout = httpx.Timeout(settings.fetch_timeout_seconds)
    with httpx.Client(
        transport=PublicHTTPTransport(),
        headers=headers,
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for _ in range(settings.fetch_max_redirects + 1):
            current = validate_resource_target(current)
            parts = urlsplit(current)
            expected = resolve_public_addresses(
                _hostname(parts),
                parts.port or (443 if parts.scheme == "https" else 80),
            )
            response, body = _request_resource_with_limit(
                client,
                current,
                max_bytes,
                expected,
            )
            if response.status_code in {301, 302, 303, 307, 308}:
                location = response.headers.get("location")
                if not location:
                    raise FetchError(
                        "bad_image_redirect",
                        "Recipe image returned an empty redirect",
                    )
                current = validate_resource_target(urljoin(current, location))
                continue
            if _is_browser_challenge(response):
                raise FetchError(
                    "image_browser_challenge",
                    "Recipe image requires browser verification",
                )
            if response.status_code >= 400:
                raise FetchError(
                    "image_fetch_status",
                    f"Recipe image returned HTTP {response.status_code}",
                )
            return ResourceFetchResult(
                body=body,
                url=current,
                content_type=response.headers.get("content-type", "").partition(";")[0].casefold(),
            )
    raise FetchError("too_many_image_redirects", "Recipe image redirected too many times")
