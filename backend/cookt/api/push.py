"""Web Push endpoints: the VAPID public key, device subscriptions, and timer alert schedules."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import db, push

router = APIRouter(prefix="/api/push")


@router.get("/key")
def key() -> dict[str, str]:
    return {"key": push.application_server_key()}


class Keys(BaseModel):
    p256dh: str
    auth: str


class Subscription(BaseModel):
    endpoint: str = Field(max_length=1024)
    keys: Keys


@router.post("/subscribe")
def subscribe(body: Subscription, request: Request) -> dict[str, Any]:
    try:
        push.subscribe(
            db.get_conn(),
            body.endpoint,
            body.keys.p256dh,
            body.keys.auth,
            request.headers.get("user-agent"),
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True}


class Endpoint(BaseModel):
    endpoint: str = Field(max_length=1024)


@router.post("/unsubscribe")
def unsubscribe(body: Endpoint) -> dict[str, Any]:
    push.unsubscribe(db.get_conn(), body.endpoint)
    return {"ok": True}


class TimerAlert(BaseModel):
    id: str = Field(max_length=64)
    label: str = Field(max_length=120)
    recipe_title: str = Field(max_length=200)
    ends_at: float  # unix milliseconds, like Date.now()


class Schedule(BaseModel):
    endpoint: str = Field(max_length=1024)
    timers: list[TimerAlert] = Field(max_length=50)


@router.post("/timers")
def timers(body: Schedule) -> dict[str, Any]:
    try:
        count = push.schedule(db.get_conn(), body.endpoint, [t.model_dump() for t in body.timers])
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {"scheduled": count}


@router.post("/test")
def test(body: Endpoint) -> dict[str, Any]:
    row = (
        db.get_conn()
        .execute("SELECT * FROM push_subscriptions WHERE endpoint = ?", (body.endpoint,))
        .fetchone()
    )
    if row is None:
        raise HTTPException(404, "unknown subscription")
    status = push.send(
        row,
        {
            "title": "cookt",
            "body": "Timer alerts are on for this device.",
            "tag": "test",
            "url": "/",
        },
    )
    if status in (404, 410):
        push.unsubscribe(db.get_conn(), body.endpoint)
    return {"status": status, "ok": 200 <= status < 300}
