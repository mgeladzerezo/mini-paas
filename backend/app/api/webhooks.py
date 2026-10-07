import json
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.deps import BoxDep, DbDep, SettingsDep
from app.metrics import WEBHOOK_DELIVERIES
from app.models import App, GithubInstallation, WebhookDelivery
from app.security import SecretBox, verify_github_signature
from app.services.releases import create_build_release

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

BRANCH_PREFIX = "refs/heads/"
DELETED_SHA = "0" * 40


def _handle_push(db: Session, box: SecretBox, payload: dict) -> int:
    ref = payload.get("ref", "")
    sha = payload.get("after", "")
    if not ref.startswith(BRANCH_PREFIX) or payload.get("deleted") or sha == DELETED_SHA:
        return 0
    head_commit = payload.get("head_commit") or {}
    message = (head_commit.get("message") or "").partition("\n")[0]
    pusher = (payload.get("pusher") or {}).get("name", "github")

    # Matching on the installation as well as the repo name means a delivery can only
    # ever deploy apps that were connected through that same installation.
    apps = db.scalars(
        select(App).where(
            App.repo_full_name == payload["repository"]["full_name"],
            App.branch == ref.removeprefix(BRANCH_PREFIX),
            App.installation_id == payload["installation"]["id"],
        )
    ).all()
    for app in apps:
        create_build_release(db, app, box, sha, message, pusher)
    return len(apps)


def _handle_installation(db: Session, payload: dict) -> None:
    if payload.get("action") == "deleted":
        db.execute(
            delete(GithubInstallation).where(
                GithubInstallation.installation_id == payload["installation"]["id"]
            )
        )


@router.post("/github")
async def github_webhook(
    request: Request,
    db: DbDep,
    settings: SettingsDep,
    box: BoxDep,
    x_github_event: Annotated[str, Header()] = "",
    x_github_delivery: Annotated[str, Header()] = "",
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> dict:
    # The signature covers the exact bytes GitHub sent, so verify before parsing.
    body = await request.body()
    if not verify_github_signature(settings.github_webhook_secret, body, x_hub_signature_256):
        WEBHOOK_DELIVERIES.labels(x_github_event or "unknown", "bad_signature").inc()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature")
    if not x_github_delivery:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Missing delivery id")

    # GitHub redelivers on timeouts; the primary key makes a repeat a no-op.
    db.add(WebhookDelivery(delivery_id=x_github_delivery, event=x_github_event))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        WEBHOOK_DELIVERIES.labels(x_github_event, "duplicate").inc()
        return {"status": "duplicate", "releases": 0}

    try:
        payload = json.loads(body)
        created = 0
        if x_github_event == "push":
            created = _handle_push(db, box, payload)
        elif x_github_event == "installation":
            _handle_installation(db, payload)
    except (AttributeError, KeyError, TypeError, ValueError):
        db.rollback()
        WEBHOOK_DELIVERIES.labels(x_github_event, "malformed").inc()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Malformed payload") from None

    db.commit()
    WEBHOOK_DELIVERIES.labels(x_github_event, "accepted").inc()
    return {"status": "accepted", "releases": created}
