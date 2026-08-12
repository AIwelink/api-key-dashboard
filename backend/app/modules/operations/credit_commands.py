from __future__ import annotations

from typing import Any

from app.modules.operations.schemas import RedemptionBatchCreate
from app.modules.sub2api.client import Sub2ApiClient


SUB2API_REDEMPTION_CHUNK_SIZE = 100


class CreditCapabilityUnavailable(RuntimeError):
    code = "capability_unavailable"


class Sub2ApiCreditCommandAdapter:
    def __init__(self, *, client: Sub2ApiClient | Any) -> None:
        self.client = client

    async def create_redemption_batch(
        self,
        *,
        site: dict[str, Any],
        payload: RedemptionBatchCreate,
    ) -> dict[str, Any]:
        del site
        records: list[dict[str, Any]] = []
        remaining = payload.code_count
        chunk_index = 1
        while remaining > 0:
            count = min(remaining, SUB2API_REDEMPTION_CHUNK_SIZE)
            chunk = await self.client.generate_redemption_codes(
                count=count,
                value=payload.balance_units_per_code,
                idempotency_key=f"{payload.idempotency_key}:chunk:{chunk_index}",
            )
            records.extend(chunk)
            remaining -= count
            chunk_index += 1
        return {
            "codes": [str(item["code"]) for item in records],
            "source_batch_id": ",".join(
                str(item["id"])
                for item in records
                if item.get("id") is not None
            ),
        }


def create_credit_command_adapter(site: dict[str, Any]):
    client_type = str(site.get("client_type") or "").strip().lower()
    if client_type != "sub2api":
        raise CreditCapabilityUnavailable(
            "No verified redemption write adapter is available for this site version"
        )
    base_url = str(site.get("base_url") or "").strip()
    api_key = str(site.get("api_key") or "").strip()
    if not base_url or not api_key:
        raise CreditCapabilityUnavailable(
            "Sub2API redemption generation requires the site base URL and Admin API Key"
        )
    return Sub2ApiCreditCommandAdapter(
        client=Sub2ApiClient(base_url=base_url, token=api_key)
    )
