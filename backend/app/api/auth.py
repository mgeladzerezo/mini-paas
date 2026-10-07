from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.deps import DbDep, SettingsDep, UserDep
from app.models import Tenant, User
from app.schemas import LoginRequest, PlatformInfo, SignupRequest, TokenResponse, UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/api", tags=["auth"])


def _token_for(user: User, settings: SettingsDep) -> TokenResponse:
    token = create_access_token(user.id, settings.jwt_secret, settings.jwt_ttl_minutes)
    return TokenResponse(access_token=token)


@router.post("/auth/signup", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
def signup(body: SignupRequest, db: DbDep, settings: SettingsDep) -> TokenResponse:
    tenant = Tenant(slug=body.tenant_slug, name=body.tenant_name)
    user = User(email=body.email.lower(), password_hash=hash_password(body.password), tenant=tenant)
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Email or workspace name is already taken"
        ) from None
    return _token_for(user, settings)


@router.post("/auth/login", response_model=TokenResponse)
def login(body: LoginRequest, db: DbDep, settings: SettingsDep) -> TokenResponse:
    user = db.scalar(select(User).where(User.email == body.email.lower()))
    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid email or password")
    return _token_for(user, settings)


@router.get("/auth/me", response_model=UserOut)
def me(user: UserDep) -> User:
    return user


@router.get("/platform", response_model=PlatformInfo)
def platform_info(settings: SettingsDep, user: UserDep) -> PlatformInfo:
    if settings.adapters == "fake":
        # Skips github.com entirely: the demo GitHub client accepts this code.
        install_url = f"/github/callback?installation_id={user.tenant_id}&code=dev-{user.tenant_id}"
    else:
        install_url = f"https://github.com/apps/{settings.github_app_slug}/installations/new"
    return PlatformInfo(
        base_domain=settings.base_domain,
        ingress_target=settings.ingress_target,
        github_install_url=install_url,
    )
