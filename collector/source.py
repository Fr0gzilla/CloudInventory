"""Common contract for offline inventory sources.

The source records omit id and run_id: those are assigned when persisting a run.
The role is not inferred by a source and remains unset until consolidation.
"""

from typing import Generic, Protocol, TypedDict, TypeVar


class AssetRecord(TypedDict):
    vm_id: str
    vm_name: str
    fqdn: str
    ip_reported: str
    node: str
    type: str
    status: str
    tags: str
    role: str | None
    os: str
    annotation: str
    cpu: float
    ram: float
    disk: float
    uptime: float


class IpamRecord(TypedDict):
    ip: str
    dns_name: str
    status: str
    tenant: str
    site: str
    tags: str
    duplicate_dns: int
    duplicate_ip: int
    meta_zone: str


RecordT = TypeVar("RecordT")


class Source(Protocol, Generic[RecordT]):
    """Collect a fresh, independently mutable list of source observations."""

    def collect(self) -> list[RecordT]: ...
