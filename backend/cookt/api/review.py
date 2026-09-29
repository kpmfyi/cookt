"""Review page: copy-edit proposals, approve / reject / keep / delete, each undoable."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import copyedit, db

router = APIRouter(prefix="/api/review")


def _conn():
    return db.get_conn()


def _recipe_id(key: str) -> str:
    row = _conn().execute("SELECT id FROM recipes WHERE id = ? OR slug = ?", (key, key)).fetchone()
    if row is None:
        raise HTTPException(404, "recipe not found")
    return row["id"]


@router.get("")
def items() -> dict[str, Any]:
    return {"items": copyedit.review_items(_conn()), "prompt_version": copyedit.PROMPT_VERSION}


class ApproveBody(BaseModel):
    accepted: list[str] | None = None  # edit keys; None = all
    person_id: str | None = None


@router.post("/{key}/approve")
def approve(key: str, body: ApproveBody) -> dict[str, Any]:
    try:
        return copyedit.approve(_conn(), _recipe_id(key), body.accepted, body.person_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{key}/reject")
def reject(key: str) -> dict[str, Any]:
    return copyedit.reject(_conn(), _recipe_id(key))


@router.post("/{key}/keep")
def keep(key: str) -> dict[str, Any]:
    return copyedit.keep(_conn(), _recipe_id(key))


@router.post("/{key}/delete")
def delete(key: str) -> dict[str, Any]:
    return copyedit.delete(_conn(), _recipe_id(key))


class PersonBody(BaseModel):
    person_id: str | None = None


@router.post("/{key}/undo")
def undo(key: str, body: PersonBody) -> dict[str, Any]:
    try:
        return copyedit.undo(_conn(), _recipe_id(key), body.person_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
