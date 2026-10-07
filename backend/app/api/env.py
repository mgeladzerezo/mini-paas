from fastapi import APIRouter, HTTPException, status

from app.deps import BoxDep, DbDep, OwnedAppDep
from app.models import App, EnvVar
from app.schemas import EnvUpdate, EnvVarOut
from app.security import SecretBox

router = APIRouter(prefix="/api/apps/{app_id}/env", tags=["env"])


def env_out(app: App, box: SecretBox) -> list[EnvVarOut]:
    return [
        EnvVarOut(
            key=var.key,
            value=None if var.is_secret else box.decrypt(var.value_encrypted),
            is_secret=var.is_secret,
        )
        for var in sorted(app.env_vars, key=lambda var: var.key)
    ]


@router.get("", response_model=list[EnvVarOut])
def get_env(app: OwnedAppDep, box: BoxDep) -> list[EnvVarOut]:
    return env_out(app, box)


@router.put("", response_model=list[EnvVarOut])
def replace_env(body: EnvUpdate, app: OwnedAppDep, db: DbDep, box: BoxDep) -> list[EnvVarOut]:
    """Replace the app's variables with the submitted set.

    Takes effect on the next release; running pods keep the config they started with.
    """
    existing = {var.key: var for var in app.env_vars}
    updated: list[EnvVar] = []
    for item in body.variables:
        current = existing.get(item.key)
        if item.value is None and current is None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"{item.key} needs a value")
        if current is None:
            current = EnvVar(key=item.key, value_encrypted=box.encrypt(item.value or ""))
        elif item.value is not None:
            current.value_encrypted = box.encrypt(item.value)
        # A secret can be replaced but never downgraded to a readable variable
        # without supplying its value again.
        if current.is_secret and not item.is_secret and item.value is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"{item.key}: re-enter the value to make a secret readable",
            )
        current.is_secret = item.is_secret
        updated.append(current)
    app.env_vars = updated
    db.commit()
    return env_out(app, box)
