"""AKSynxa Python SDK — Public REST API v1 ka chhota sa client.

Sirf `httpx` par based (jo AKSynxa ke requirements me pehle se hai).
API key jis brand ki hai, client sirf usi brand ka data layega.

Usage:
    from aksynxa_client import AKSynxaClient

    client = AKSynxaClient("https://aksynxa-api.onrender.com",
                           api_key="aks_...")
    print(client.stats())
    for lead in client.leads(limit=5)["leads"]:
        print(lead["name"], lead["stage"])
    reply = client.simulate("9198...", "hi")
    print(reply["replies"])

Errors par AKSynxaAPIError uthta hai (status code ke saath).
"""
import httpx

__all__ = ["AKSynxaClient", "AKSynxaAPIError"]
__version__ = "1.0.0"


class AKSynxaAPIError(RuntimeError):
    def __init__(self, status_code: int, detail: str = ""):
        super().__init__(f"AKSynxa API error {status_code}: {detail}")
        self.status_code = status_code
        self.detail = detail


class AKSynxaClient:
    """AKSynxa Public API v1 client (per-brand API key)."""

    def __init__(self, base_url: str, api_key: str, timeout: float = 30.0,
                 transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self.base_url,
            headers={"X-API-Key": api_key},
            timeout=timeout,
            transport=transport,
        )

    # -- internals ------------------------------------------------------
    def _get(self, path: str, **params) -> dict:
        resp = self._client.get(path, params=params or None)
        return self._unwrap(resp)

    def _post(self, path: str, json_body: dict) -> dict:
        resp = self._client.post(path, json=json_body)
        return self._unwrap(resp)

    @staticmethod
    def _unwrap(resp: httpx.Response) -> dict:
        if resp.status_code >= 400:
            try:
                detail = resp.json().get("detail", resp.text)
            except Exception:
                detail = resp.text
            raise AKSynxaAPIError(resp.status_code, str(detail))
        return resp.json()

    # -- public API -------------------------------------------------------
    def stats(self) -> dict:
        """Dashboard totals: total_leads, bookings, pending_reminders..."""
        return self._get("/api/v1/stats")

    def leads(self, limit: int = 20) -> dict:
        """Recent leads (naye pehle): {"brand", "count", "leads": [...]}"""
        return self._get("/api/v1/leads", limit=limit)

    def bookings(self, limit: int = 20) -> dict:
        """Recent confirmed bookings: {"brand", "count", "bookings": [...]}"""
        return self._get("/api/v1/bookings", limit=limit)

    def reminders(self, status: str = "pending") -> dict:
        """Reminders: status = pending | sent | cancelled | all"""
        return self._get("/api/v1/reminders", status=status)

    def simulate(self, from_phone: str, text: str) -> dict:
        """Bot ko test message — bilkul website demo jaisa reply milega.
        Asli WhatsApp message NAHI jata (demo tool hai)."""
        return self._post("/api/v1/simulate",
                          {"from": from_phone, "text": text})

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
