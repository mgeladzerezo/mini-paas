from dataclasses import dataclass

from app.adapters.builder import Builder
from app.adapters.dns import DnsResolver
from app.adapters.github import GitHubClient
from app.adapters.kube import KubeClient
from app.config import Settings


@dataclass
class Adapters:
    kube: KubeClient
    builder: Builder
    github: GitHubClient
    dns: DnsResolver


def build_adapters(settings: Settings) -> Adapters:
    if settings.adapters == "fake":
        from app.adapters.fake import DemoGitHub, FakeBuilder, FakeDns, FakeKube

        return Adapters(
            kube=FakeKube(auto_ready=True),
            builder=FakeBuilder(settings.registry_repository),
            github=DemoGitHub(),
            dns=FakeDns(),
        )

    from app.adapters.builder import KanikoJobBuilder
    from app.adapters.dns import SystemDnsResolver
    from app.adapters.github import GitHubAppClient
    from app.adapters.kube import DynamicKubeClient

    kube = DynamicKubeClient()
    return Adapters(
        kube=kube,
        builder=KanikoJobBuilder(
            kube=kube,
            namespace=settings.builds_namespace,
            service_account=settings.builder_service_account,
            registry_repository=settings.registry_repository,
            timeout_seconds=settings.build_timeout_seconds,
        ),
        github=GitHubAppClient(
            app_id=settings.github_app_id,
            private_key=settings.github_private_key,
            client_id=settings.github_client_id,
            client_secret=settings.github_client_secret,
        ),
        dns=SystemDnsResolver(),
    )
