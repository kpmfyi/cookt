"""Web Push for cook timers, so "timer done" reaches a locked iPad.

A home-screen web app is suspended in the background, so the page cannot notify when a timer
ends. The page instead sends its running timers here; a small sender thread delivers each one
as a Web Push message at its end time through the browser vendor's push service (Apple's for
iPad). Payloads are encrypted per RFC 8291 (aes128gcm) and signed with a VAPID key (RFC 8292)
kept in data/vapid.pem. Only the timer label and recipe title leave the box.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sqlite3
import struct
import threading
import time
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from . import config, db

log = logging.getLogger("cookt.push")

# The server POSTs to whatever endpoint a browser hands it, so only the real push services
# are allowed (no requests to arbitrary hosts).
PUSH_HOSTS = (
    "push.apple.com",
    "fcm.googleapis.com",
    "push.services.mozilla.com",
    "notify.windows.com",
)
TTL_SECONDS = 600  # a timer alert older than this is useless


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def allowed_endpoint(endpoint: str) -> bool:
    parts = urlsplit(endpoint)
    host = parts.hostname or ""
    return parts.scheme == "https" and any(
        host == allowed or host.endswith("." + allowed) for allowed in PUSH_HOSTS
    )


# --- keys ---------------------------------------------------------------------------------


_key_lock = threading.Lock()


def vapid_key() -> ec.EllipticCurvePrivateKey:
    path = config.settings.data_dir / "vapid.pem"  # read late: tests reload config
    with _key_lock:
        if not path.exists():
            key = ec.generate_private_key(ec.SECP256R1())
            pem = key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(pem)
        loaded = serialization.load_pem_private_key(path.read_bytes(), password=None)
    assert isinstance(loaded, ec.EllipticCurvePrivateKey)
    return loaded


def _raw_public(key: ec.EllipticCurvePrivateKey | ec.EllipticCurvePublicKey) -> bytes:
    public = key.public_key() if isinstance(key, ec.EllipticCurvePrivateKey) else key
    return public.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )


def application_server_key() -> str:
    """The public key the browser needs for pushManager.subscribe (base64url, 65 bytes)."""
    return b64u(_raw_public(vapid_key()))


def vapid_headers(endpoint: str, key: ec.EllipticCurvePrivateKey | None = None) -> dict[str, str]:
    key = key or vapid_key()
    parts = urlsplit(endpoint)
    header = b64u(json.dumps({"typ": "JWT", "alg": "ES256"}, separators=(",", ":")).encode())
    claims = {
        "aud": f"{parts.scheme}://{parts.netloc}",
        "exp": int(time.time()) + 12 * 3600,
        "sub": config.settings.vapid_subject,
    }
    body = b64u(json.dumps(claims, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}".encode()
    r, s = decode_dss_signature(key.sign(signing_input, ec.ECDSA(hashes.SHA256())))
    signature = b64u(r.to_bytes(32, "big") + s.to_bytes(32, "big"))
    return {"Authorization": f"vapid t={header}.{body}.{signature}, k={b64u(_raw_public(key))}"}


# --- RFC 8291 payload encryption ----------------------------------------------------------


def _hmac(key: bytes, data: bytes) -> bytes:
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(data)
    return mac.finalize()


def encrypt(
    payload: bytes,
    p256dh: str,
    auth: str,
    *,
    salt: bytes | None = None,
    server_key: ec.EllipticCurvePrivateKey | None = None,
) -> bytes:
    """One aes128gcm record: header (salt, rs, keyid = our ephemeral public key) + ciphertext."""
    ua_public = unb64u(p256dh)
    auth_secret = unb64u(auth)
    salt = salt or os.urandom(16)
    server_key = server_key or ec.generate_private_key(ec.SECP256R1())
    as_public = _raw_public(server_key)
    ua_key = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public)
    shared = server_key.exchange(ec.ECDH(), ua_key)
    prk_key = _hmac(auth_secret, shared)
    ikm = _hmac(prk_key, b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)
    return salt + struct.pack("!IB", 4096, len(as_public)) + as_public + ciphertext


def send(subscription: sqlite3.Row | dict, message: dict, *, urgency: str = "high") -> int:
    """POST one encrypted message; returns the push service's HTTP status (0 on network error)."""
    endpoint = subscription["endpoint"]
    if not allowed_endpoint(endpoint):
        return 0
    body = encrypt(json.dumps(message).encode(), subscription["p256dh"], subscription["auth"])
    headers = {
        **vapid_headers(endpoint),
        "Content-Encoding": "aes128gcm",
        "Content-Type": "application/octet-stream",
        "TTL": str(TTL_SECONDS),
        "Urgency": urgency,
    }
    try:
        response = httpx.post(endpoint, content=body, headers=headers, timeout=15)
    except httpx.HTTPError as exc:
        log.warning("push to %s failed: %s", urlsplit(endpoint).hostname, exc)
        return 0
    if response.status_code >= 300:
        log.warning(
            "push to %s: %s %s",
            urlsplit(endpoint).hostname,
            response.status_code,
            response.text[:200],
        )
    return response.status_code


# --- subscriptions and scheduled timer alerts ---------------------------------------------


def subscribe(
    conn: sqlite3.Connection, endpoint: str, p256dh: str, auth: str, user_agent: str | None
) -> None:
    if not allowed_endpoint(endpoint):
        raise ValueError("not a known push service")
    if len(unb64u(p256dh)) != 65 or len(unb64u(auth)) != 16:
        raise ValueError("bad subscription keys")
    conn.execute(
        """INSERT INTO push_subscriptions(endpoint, p256dh, auth, user_agent, created_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(endpoint) DO UPDATE SET p256dh = excluded.p256dh, auth = excluded.auth,
             user_agent = excluded.user_agent""",
        (endpoint, p256dh, auth, user_agent, db.now()),
    )


def unsubscribe(conn: sqlite3.Connection, endpoint: str) -> None:
    conn.execute("DELETE FROM push_subscriptions WHERE endpoint = ?", (endpoint,))


def schedule(conn: sqlite3.Connection, endpoint: str, timers: list[dict]) -> int:
    """Replace this device's pending alerts with its current running timers."""
    if (
        conn.execute("SELECT 1 FROM push_subscriptions WHERE endpoint = ?", (endpoint,)).fetchone()
        is None
    ):
        raise LookupError("unknown subscription")
    keep = [str(t["id"]) for t in timers]
    with db.tx(conn):
        conn.execute(
            f"DELETE FROM push_timers WHERE endpoint = ? AND sent_at IS NULL "
            f"AND timer_id NOT IN ({','.join('?' * len(keep)) or 'NULL'})",
            (endpoint, *keep),
        )
        for timer in timers:
            conn.execute(
                """INSERT INTO push_timers(endpoint, timer_id, ends_at, title, body)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(endpoint, timer_id) DO UPDATE SET ends_at = excluded.ends_at,
                     title = excluded.title, body = excluded.body, sent_at = NULL""",
                (
                    endpoint,
                    str(timer["id"]),
                    float(timer["ends_at"]) / 1000,
                    "Timer done",
                    f"{timer['label']} — {timer['recipe_title']}"[:200],
                ),
            )
    return len(timers)


def deliver_due(conn: sqlite3.Connection, now: float | None = None) -> int:
    now = time.time() if now is None else now
    rows = conn.execute(
        """SELECT t.endpoint, t.timer_id, t.ends_at, t.title, t.body, s.p256dh, s.auth
           FROM push_timers t JOIN push_subscriptions s ON s.endpoint = t.endpoint
           WHERE t.sent_at IS NULL AND t.ends_at <= ?""",
        (now,),
    ).fetchall()
    for row in rows:
        conn.execute(
            "UPDATE push_timers SET sent_at = ? WHERE endpoint = ? AND timer_id = ?",
            (db.now(), row["endpoint"], row["timer_id"]),
        )
        if now - row["ends_at"] > TTL_SECONDS:
            continue
        status = send(
            row, {"title": row["title"], "body": row["body"], "tag": row["timer_id"], "url": "/"}
        )
        if status in (404, 410):  # the browser dropped this subscription
            unsubscribe(conn, row["endpoint"])
    conn.execute(
        "DELETE FROM push_timers WHERE sent_at IS NOT NULL AND ends_at < ?", (now - 86400,)
    )
    return len(rows)


class Sender:
    """Background thread: checks for due timer alerts once a second."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="cookt-push", daemon=True)

    def start(self) -> Sender:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        conn = db.connect()
        while not self._stop.wait(1.0):
            try:
                deliver_due(conn)
            except Exception:  # keep the sender alive; one bad row must not stop alerts
                log.exception("push sender")
        conn.close()
