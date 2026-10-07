from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy.exc import IntegrityError

from app.deps import AdaptersDep, DbDep, DeployerDep, OwnedAppDep, OwnedDomainDep, SettingsDep
from app.models import Domain, utcnow
from app.schemas import DomainCreate, DomainOut
from app.services import domains as domain_service

router = APIRouter(prefix="/api/apps/{app_id}/domains", tags=["domains"])


def domain_out(domain: Domain, ingress_target: str) -> DomainOut:
    return DomainOut(
        id=domain.id,
        hostname=domain.hostname,
        verified=domain.verified_at is not None,
        txt_record_name=domain_service.txt_record_name(domain.hostname),
        txt_record_value=domain_service.txt_record_value(domain.verification_token),
        cname_target=ingress_target,
        dns_status=domain.dns_status,
        tls_status=domain.tls_status,
        last_checked_at=domain.last_checked_at,
    )


@router.get("", response_model=list[DomainOut])
def list_domains(app: OwnedAppDep, settings: SettingsDep) -> list[DomainOut]:
    return [domain_out(domain, settings.ingress_target) for domain in app.domains]


@router.post("", response_model=DomainOut, status_code=status.HTTP_201_CREATED)
def add_domain(body: DomainCreate, app: OwnedAppDep, db: DbDep, settings: SettingsDep) -> DomainOut:
    try:
        hostname = domain_service.normalize_hostname(body.hostname)
    except domain_service.DomainError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if domain_service.is_platform_hostname(hostname, settings.base_domain, settings.ingress_target):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Platform hostnames cannot be attached"
        )

    domain = Domain(
        app_id=app.id,
        hostname=hostname,
        verification_token=domain_service.new_verification_token(),
    )
    db.add(domain)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Domain is already attached to this app"
        ) from None
    return domain_out(domain, settings.ingress_target)


@router.post("/{domain_id}/check", response_model=DomainOut)
def check_domain(
    app: OwnedAppDep,
    domain: OwnedDomainDep,
    db: DbDep,
    settings: SettingsDep,
    adapters: AdaptersDep,
    deployer: DeployerDep,
) -> DomainOut:
    """Re-run ownership, routing and certificate checks for one domain."""
    now = utcnow()
    try:
        newly_verified = domain_service.try_verify(db, domain, adapters.dns, now)
    except domain_service.DomainError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from None

    if domain.verified_at is not None:
        routed = domain_service.points_at_platform(
            domain.hostname, settings.ingress_target, adapters.dns
        )
        domain.dns_status = "ok" if routed else "pending"
        issued = deployer.certificate_ready(app, domain.hostname)
        domain.tls_status = "issued" if issued else "pending"
    domain.last_checked_at = now
    db.commit()

    if newly_verified:
        deployer.sync_ingress(app)
    return domain_out(domain, settings.ingress_target)


@router.delete("/{domain_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_domain(
    app: OwnedAppDep, domain: OwnedDomainDep, db: DbDep, deployer: DeployerDep
) -> Response:
    was_verified = domain.verified_at is not None
    app.domains.remove(domain)
    db.commit()
    if was_verified:
        deployer.sync_ingress(app)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
