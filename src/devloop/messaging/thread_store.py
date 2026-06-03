"""ConfigMap-backed thread ↔ workflow mapping (issue #29).

Provides durable storage for ``workflow_id ↔ thread_id`` mappings so that
messaging activity can resolve a thread after a bot pod restart.

Usage (per-platform):
    from devloop.messaging.thread_store import ConfigMapThreadStore

    store = ConfigMapThreadStore(configmap_name="my-bot-threads")
    store.put("wf-001", "thread-abc")
    tid = store.get_thread("wf-001")
"""

from __future__ import annotations

import json
import logging
from typing import Callable

from kubernetes import client as k8s_client

log = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

_W2T_KEY = "workflow-to-thread"
_T2W_KEY = "thread-to-workflow"

# Module-level singleton — initialized once on first use.
_api: k8s_client.CoreV1Api | None = None


def _v1() -> k8s_client.CoreV1Api:
    """Return the shared Kubernetes CoreV1Api client, initializing it once."""
    global _api
    if _api is None:
        try:
            k8s_client.config.load_incluster_config()
        except k8s_client.ConfigException:
            k8s_client.config.load_kube_config()
        _api = k8s_client.CoreV1Api()
    return _api


# --------------------------------------------------------------------------- #
# ConfigMapThreadStore
# --------------------------------------------------------------------------- #


class ConfigMapThreadStore:
    """Kubernetes ConfigMap-backed bidirectional workflow↔thread mapping.

    The ConfigMap stores two JSON keys::

        {
            "workflow-to-thread": {"wf-001": "thread-abc"},
            "thread-to-workflow": {"thread-abc": "wf-001"},
        }

    Both directions are stored explicitly for O(1) lookups. The reverse
    key defaults to ``thread_id`` but can be overridden — e.g. Slack stores
    a ``channel:thread_ts`` composite as the forward thread_id but receives
    replies keyed only by ``thread_ts``.

    Writes use a read-modify-replace loop with Kubernetes ``resourceVersion``
    optimistic concurrency to prevent lost-update races under concurrent
    activity executions.
    """

    def __init__(
        self,
        configmap_name: str = "bot-threads",
        namespace: str = "default",
    ) -> None:
        self._name = configmap_name
        self._namespace = namespace

    # -- public API ---------------------------------------------------------

    def put(
        self, workflow_id: str, thread_id: str, reverse_key: str | None = None
    ) -> None:
        """Store the mapping *workflow_id → thread_id*.

        *reverse_key* overrides the key used for the reverse (thread→workflow)
        lookup. Defaults to *thread_id* when omitted.
        """
        rkey = reverse_key if reverse_key is not None else thread_id

        def _apply(w2t: dict, t2w: dict) -> tuple[dict, dict]:
            w2t[workflow_id] = thread_id
            t2w[rkey] = workflow_id
            return w2t, t2w

        self._update(_apply)
        log.info("stored %s → %s", workflow_id, thread_id)

    def get_thread(self, workflow_id: str) -> str | None:
        """Return the thread_id for *workflow_id*, or ``None``."""
        w2t, _ = self._read_maps(_v1())
        return w2t.get(workflow_id)

    def get_workflow(self, thread_id: str) -> str | None:
        """Return the workflow_id that owns *thread_id*, or ``None``."""
        _, t2w = self._read_maps(_v1())
        return t2w.get(thread_id)

    def delete(self, workflow_id: str) -> None:
        """Remove the mapping for *workflow_id* and all associated reverse keys."""

        def _apply(w2t: dict, t2w: dict) -> tuple[dict, dict]:
            w2t.pop(workflow_id, None)
            # Remove all reverse entries that point to this workflow, covering
            # cases where reverse_key differs from thread_id (e.g. Slack).
            for key in [k for k, v in t2w.items() if v == workflow_id]:
                del t2w[key]
            return w2t, t2w

        self._update(_apply)
        log.info("deleted %s", workflow_id)

    # -- internal helpers ---------------------------------------------------

    def _read_maps(self, api: k8s_client.CoreV1Api) -> tuple[dict, dict]:
        """Read the ConfigMap and return (w2t, t2w) dicts."""
        try:
            cm = api.read_namespaced_config_map(self._name, self._namespace)
            data = cm.data or {}
            w2t = json.loads(data.get(_W2T_KEY, "{}"))
            t2w = json.loads(data.get(_T2W_KEY, "{}"))
            return w2t, t2w
        except k8s_client.ApiException as exc:
            if exc.status == 404:
                return {}, {}
            raise

    def _update(self, fn: Callable[[dict, dict], tuple[dict, dict]]) -> None:
        """Apply *fn* to (w2t, t2w) with optimistic-locking retry on conflict.

        Uses ``replace_namespaced_config_map`` (HTTP PUT) with the observed
        ``resourceVersion`` so concurrent writers get a 409 Conflict instead of
        silently overwriting each other.  Retries up to 5 times before raising.
        """
        for attempt in range(5):
            api = _v1()
            resource_version: str | None = None
            try:
                cm = api.read_namespaced_config_map(self._name, self._namespace)
                data = cm.data or {}
                w2t = json.loads(data.get(_W2T_KEY, "{}"))
                t2w = json.loads(data.get(_T2W_KEY, "{}"))
                resource_version = cm.metadata.resource_version
            except k8s_client.ApiException as exc:
                if exc.status != 404:
                    raise
                w2t, t2w = {}, {}

            w2t, t2w = fn(w2t, t2w)

            metadata = k8s_client.V1ObjectMeta(
                name=self._name,
                namespace=self._namespace,
            )
            if resource_version:
                metadata.resource_version = resource_version

            new_cm = k8s_client.V1ConfigMap(
                metadata=metadata,
                data={
                    _W2T_KEY: json.dumps(w2t),
                    _T2W_KEY: json.dumps(t2w),
                },
            )
            try:
                if resource_version is None:
                    api.create_namespaced_config_map(self._namespace, new_cm)
                else:
                    api.replace_namespaced_config_map(
                        self._name, self._namespace, new_cm
                    )
                return
            except k8s_client.ApiException as exc:
                if exc.status == 409 and attempt < 4:
                    log.debug("ConfigMap conflict on attempt %d, retrying", attempt + 1)
                    continue
                raise

        raise RuntimeError(
            f"Failed to update ConfigMap {self._name!r} after 5 attempts"
        )
