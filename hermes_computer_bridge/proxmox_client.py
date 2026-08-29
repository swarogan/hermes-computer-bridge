from __future__ import annotations

import urllib.parse
from typing import Any, Optional


def vncwebsocket_uri(base_url: str, node: str, vmid: int, port: int, ticket: str) -> str:
    base = base_url.rstrip("/")
    base = base.replace("https://", "wss://").replace("http://", "ws://")
    query = urllib.parse.urlencode({"port": port, "vncticket": ticket})
    return f"{base}/api2/json/nodes/{node}/qemu/{vmid}/vncwebsocket?{query}"


class ProxmoxClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        verify: bool = False,
        timeout: float = 8.0,
        client: Optional[Any] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        if client is not None:
            self._client = client
        else:
            import httpx

            self._client = httpx.Client(
                verify=verify, timeout=timeout, headers={"Authorization": token}
            )

    def _get(self, path: str) -> Any:
        response = self._client.get(self.base_url + path)
        response.raise_for_status()
        return response.json().get("data")

    def version(self) -> Any:
        return self._get("/api2/json/version")

    def nodes(self) -> list[dict]:
        return self._get("/api2/json/nodes") or []

    def vms(self, node: str) -> list[dict]:
        return self._get(f"/api2/json/nodes/{node}/qemu") or []

    def vncproxy(self, node: str, vmid: int) -> dict:
        response = self._client.post(
            f"{self.base_url}/api2/json/nodes/{node}/qemu/{vmid}/vncproxy",
            data={"websocket": 1},
        )
        response.raise_for_status()
        return response.json()["data"]

    def websocket_uri(self, node: str, vmid: int, port: int, ticket: str) -> str:
        return vncwebsocket_uri(self.base_url, node, vmid, port, ticket)


__all__ = ["ProxmoxClient", "vncwebsocket_uri"]
