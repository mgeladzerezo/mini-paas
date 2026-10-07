import re
import secrets
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.dns import DnsResolver
from app.models import App, Domain

HOSTNAME_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?$"
)
TXT_PREFIX = "_paas-verify"


class DomainError(Exception):
    pass


def normalize_hostname(raw: str) -> str:
    hostname = raw.strip().lower().rstrip(".")
    if not HOSTNAME_RE.match(hostname):
        raise DomainError("not a valid hostname")
    return hostname


def is_platform_hostname(hostname: str, base_domain: str, ingress_target: str) -> bool:
    return hostname in (base_domain, ingress_target) or hostname.endswith(f".{base_domain}")


def new_verification_token() -> str:
    return secrets.token_hex(16)


def txt_record_name(hostname: str) -> str:
    return f"{TXT_PREFIX}.{hostname}"


def txt_record_value(token: str) -> str:
    return f"paas-verify={token}"


def verified_hostnames(app: App) -> list[str]:
    """The only source of custom hostnames for an Ingress."""
    return sorted(d.hostname for d in app.domains if d.verified_at is not None)


def owns_domain(domain: Domain, resolver: DnsResolver) -> bool:
    expected = txt_record_value(domain.verification_token)
    return expected in resolver.txt(txt_record_name(domain.hostname))


def points_at_platform(hostname: str, ingress_target: str, resolver: DnsResolver) -> bool:
    if resolver.cname(hostname) == ingress_target:
        return True
    # Apex domains cannot have a CNAME, so also accept A/AAAA records (or an ALIAS)
    # that resolve to the same addresses as the ingress load balancer.
    target_addresses = set(resolver.addresses(ingress_target))
    return bool(target_addresses & set(resolver.addresses(hostname)))


def verified_elsewhere(session: Session, domain: Domain) -> bool:
    other = session.scalar(
        select(Domain.id).where(
            Domain.hostname == domain.hostname,
            Domain.id != domain.id,
            Domain.verified_at.is_not(None),
        )
    )
    return other is not None


def try_verify(session: Session, domain: Domain, resolver: DnsResolver, now: datetime) -> bool:
    """Mark the domain verified if the TXT record proves ownership. Returns True if it changed."""
    if domain.verified_at is not None:
        return False
    if not owns_domain(domain, resolver):
        return False
    # Routing is by hostname, so two apps can never both serve the same one.
    if verified_elsewhere(session, domain):
        raise DomainError("this hostname is already verified for another app")
    domain.verified_at = now
    return True
