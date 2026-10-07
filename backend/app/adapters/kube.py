from typing import Protocol


class KubeClient(Protocol):
    """The handful of cluster operations the platform needs.

    Objects are plain dicts in manifest form. `namespace` is None for cluster-scoped kinds.
    """

    def apply(self, manifest: dict) -> None: ...

    def get(self, api_version: str, kind: str, namespace: str | None, name: str) -> dict | None: ...

    def list_by_label(
        self, api_version: str, kind: str, namespace: str, label_selector: str
    ) -> list[dict]: ...

    def delete(self, api_version: str, kind: str, namespace: str | None, name: str) -> None: ...

    def pod_logs(self, namespace: str, pod_name: str, container: str) -> str: ...


class DynamicKubeClient:
    """KubeClient backed by the official client, using server-side apply.

    Server-side apply makes every write idempotent and lets the HPA own
    `spec.replicas` without the platform fighting it on the next deploy.
    """

    FIELD_MANAGER = "mini-paas"

    def __init__(self) -> None:
        from kubernetes import client, config, dynamic

        try:
            config.load_incluster_config()
        except config.ConfigException:
            config.load_kube_config()
        api_client = client.ApiClient()
        self._dynamic = dynamic.DynamicClient(api_client)
        self._core = client.CoreV1Api(api_client)

    def _resource(self, api_version: str, kind: str):
        return self._dynamic.resources.get(api_version=api_version, kind=kind)

    def apply(self, manifest: dict) -> None:
        metadata = manifest["metadata"]
        self._dynamic.server_side_apply(
            self._resource(manifest["apiVersion"], manifest["kind"]),
            body=manifest,
            name=metadata["name"],
            namespace=metadata.get("namespace"),
            field_manager=self.FIELD_MANAGER,
            force_conflicts=True,
        )

    def get(self, api_version: str, kind: str, namespace: str | None, name: str) -> dict | None:
        from kubernetes.dynamic.exceptions import NotFoundError

        try:
            found = self._dynamic.get(
                self._resource(api_version, kind), name=name, namespace=namespace
            )
        except NotFoundError:
            return None
        return found.to_dict()

    def list_by_label(
        self, api_version: str, kind: str, namespace: str, label_selector: str
    ) -> list[dict]:
        found = self._dynamic.get(
            self._resource(api_version, kind), namespace=namespace, label_selector=label_selector
        )
        return found.to_dict().get("items", [])

    def delete(self, api_version: str, kind: str, namespace: str | None, name: str) -> None:
        from kubernetes.dynamic.exceptions import NotFoundError

        try:
            self._dynamic.delete(self._resource(api_version, kind), name=name, namespace=namespace)
        except NotFoundError:
            return

    def pod_logs(self, namespace: str, pod_name: str, container: str) -> str:
        from kubernetes.client.exceptions import ApiException

        try:
            return self._core.read_namespaced_pod_log(
                pod_name, namespace, container=container, tail_lines=2000
            )
        except ApiException:
            # The container may not have started yet; logs show up on a later poll.
            return ""
