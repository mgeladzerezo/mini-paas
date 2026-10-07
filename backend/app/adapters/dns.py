from typing import Protocol

import dns.exception
import dns.resolver


class DnsResolver(Protocol):
    def txt(self, name: str) -> list[str]: ...

    def cname(self, name: str) -> str | None: ...

    def addresses(self, name: str) -> list[str]: ...


class SystemDnsResolver:
    def __init__(self, timeout_seconds: float = 3.0) -> None:
        self._resolver = dns.resolver.Resolver()
        self._resolver.lifetime = timeout_seconds

    def _query(self, name: str, record_type: str) -> list:
        try:
            return list(self._resolver.resolve(name, record_type))
        except (dns.exception.DNSException, OSError):
            return []

    def txt(self, name: str) -> list[str]:
        # A TXT record is a sequence of byte strings that clients are expected to join.
        return [
            b"".join(record.strings).decode(errors="replace") for record in self._query(name, "TXT")
        ]

    def cname(self, name: str) -> str | None:
        records = self._query(name, "CNAME")
        return str(records[0].target).rstrip(".").lower() if records else None

    def addresses(self, name: str) -> list[str]:
        return sorted(
            {record.address for rtype in ("A", "AAAA") for record in self._query(name, rtype)}
        )
