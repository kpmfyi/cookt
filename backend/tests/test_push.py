"""Web Push for timers: RFC 8291 encryption round-trip, VAPID signature, scheduling, API."""

from __future__ import annotations

import base64
import importlib
import json
import os
import struct

import pytest
from cryptography.hazmat.primitives import hashes, hmac, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import encode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi.testclient import TestClient

ENDPOINT = "https://web.push.apple.com/QGx0ZXN0"


def b64u(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def unb64u(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _hmac(key: bytes, data: bytes) -> bytes:
    mac = hmac.HMAC(key, hashes.SHA256())
    mac.update(data)
    return mac.finalize()


def browser_keys():
    key = ec.generate_private_key(ec.SECP256R1())
    public = key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return key, b64u(public), b64u(os.urandom(16))


def decrypt(body: bytes, ua_key, auth: str) -> bytes:
    """What the browser does (RFC 8291 §3.4), written independently of cookt.push."""
    salt, (rs, idlen) = body[:16], struct.unpack("!IB", body[16:21])
    as_public = body[21 : 21 + idlen]
    ciphertext = body[21 + idlen :]
    assert rs == 4096 and idlen == 65
    ua_public = ua_key.public_key().public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    shared = ua_key.exchange(
        ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), as_public)
    )
    ikm = _hmac(_hmac(unb64u(auth), shared), b"WebPush: info\x00" + ua_public + as_public + b"\x01")
    prk = _hmac(salt, ikm)
    cek = _hmac(prk, b"Content-Encoding: aes128gcm\x00\x01")[:16]
    nonce = _hmac(prk, b"Content-Encoding: nonce\x00\x01")[:12]
    plain = AESGCM(cek).decrypt(nonce, ciphertext, None)
    assert plain.endswith(b"\x02")
    return plain[:-1]


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("COOKT_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("COOKT_RUN_WORKER", "0")
    import cookt.config

    importlib.reload(cookt.config)
    for name in ["cookt.db", "cookt.push", "cookt.api.push", "cookt.app"]:
        importlib.reload(importlib.import_module(name))
    from cookt import db, push

    conn = db.connect()
    db.init(conn)
    sent: list[tuple[str, dict]] = []
    monkeypatch.setattr(
        push, "send", lambda row, message, **_: sent.append((row["endpoint"], message)) or 201
    )
    return conn, push, sent, tmp_path


def test_encrypt_round_trips_through_a_browser_decrypt(env):
    _, push, _, _ = env
    ua_key, p256dh, auth = browser_keys()
    body = push.encrypt(b'{"title":"Timer done"}', p256dh, auth)
    assert decrypt(body, ua_key, auth) == b'{"title":"Timer done"}'


def test_vapid_jwt_verifies_with_the_public_key(env):
    _, push, _, tmp_path = env
    header = push.vapid_headers(ENDPOINT)["Authorization"]
    token, k = header.removeprefix("vapid t=").split(", k=")
    head, claims, signature = token.split(".")
    assert json.loads(unb64u(claims))["aud"] == "https://web.push.apple.com"
    assert k == push.application_server_key()
    public = ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), unb64u(k))
    raw = unb64u(signature)
    der = encode_dss_signature(int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big"))
    public.verify(der, f"{head}.{claims}".encode(), ec.ECDSA(hashes.SHA256()))
    assert (tmp_path / "vapid.pem").stat().st_mode & 0o077 == 0


def test_only_real_push_services_are_accepted(env):
    _, push, _, _ = env
    assert push.allowed_endpoint(ENDPOINT)
    assert push.allowed_endpoint("https://fcm.googleapis.com/fcm/send/abc")
    assert not push.allowed_endpoint("http://web.push.apple.com/x")
    assert not push.allowed_endpoint("https://127.0.0.1:8080/x")
    assert not push.allowed_endpoint("https://evilpush.apple.com.example.org/x")


def test_schedule_replaces_and_delivers_when_due(env):
    conn, push, sent, _ = env
    _, p256dh, auth = browser_keys()
    push.subscribe(conn, ENDPOINT, p256dh, auth, "iPad")
    push.schedule(
        conn,
        ENDPOINT,
        [
            {"id": "a", "label": "Rice", "recipe_title": "Fried Rice", "ends_at": 1000_000},
            {"id": "b", "label": "Rest", "recipe_title": "Steak", "ends_at": 5000_000},
        ],
    )
    # the page paused timer b: it disappears from the next schedule
    push.schedule(
        conn,
        ENDPOINT,
        [
            {"id": "a", "label": "Rice", "recipe_title": "Fried Rice", "ends_at": 1000_000},
        ],
    )
    assert push.deliver_due(conn, now=999) == 0
    assert push.deliver_due(conn, now=1001) == 1
    assert sent == [
        (ENDPOINT, {"title": "Timer done", "body": "Rice — Fried Rice", "tag": "a", "url": "/"})
    ]
    assert push.deliver_due(conn, now=6000) == 0  # sent once; b was cancelled


def test_api_subscribe_schedule_and_reject_unknown(env):
    conn, _, _, _ = env
    from cookt.app import app

    client = TestClient(app)
    _, p256dh, auth = browser_keys()
    assert len(unb64u(client.get("/api/push/key").json()["key"])) == 65
    bad = {"endpoint": "https://example.com/x", "keys": {"p256dh": p256dh, "auth": auth}}
    assert client.post("/api/push/subscribe", json=bad).status_code == 400
    good = {"endpoint": ENDPOINT, "keys": {"p256dh": p256dh, "auth": auth}}
    assert client.post("/api/push/subscribe", json=good).status_code == 200
    timers = [{"id": "a", "label": "Rice", "recipe_title": "Fried Rice", "ends_at": 1.0}]
    assert client.post(
        "/api/push/timers", json={"endpoint": ENDPOINT, "timers": timers}
    ).json() == {"scheduled": 1}
    unknown = {"endpoint": ENDPOINT + "x", "timers": []}
    assert client.post("/api/push/timers", json=unknown).status_code == 404


def test_encrypt_matches_the_rfc_8291_test_vector(env):
    _, push, _, _ = env
    private = int.from_bytes(unb64u("yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw"), "big")
    body = push.encrypt(
        b"When I grow up, I want to be a watermelon",
        "BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4",
        "BTBZMqHH6r4Tts7J_aSIgg",
        salt=unb64u("DGv6ra1nlYgDCS1FRnbzlw"),
        server_key=ec.derive_private_key(private, ec.SECP256R1()),
    )
    assert b64u(body) == (
        "DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS"
        "6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPTpK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Q"
        "ulcy4a-fN"
    )
