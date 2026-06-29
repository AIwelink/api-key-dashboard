from __future__ import annotations

from inspect import isawaitable
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.services.agent_capacity import read_pool_capacity
from app.services.agent_decision import decide_pool_action
from app.services.agent_probe import read_probe_summary

try:
    from langchain_core.tools import BaseTool, tool as langchain_tool
except Exception:  # noqa: BLE001 - keep the backend usable if LangChain is not installed in a local env.
    BaseTool = Any  # type: ignore[assignment]
    langchain_tool = None


class _FallbackCapability:
    def __init__(self, name: str, func: Any) -> None:
        self.name = name
        self._func = func

    async def ainvoke(self, arguments: dict[str, Any]) -> Any:
        result = self._func(**arguments)
        if isawaitable(result):
            return await result
        return result


def _agent_capability(name: str) -> Any:
    if langchain_tool is not None:
        return langchain_tool(name)

    def decorator(func: Any) -> _FallbackCapability:
        return _FallbackCapability(name, func)

    return decorator


def build_read_only_agent_capabilities(db: AsyncIOMotorDatabase) -> dict[str, BaseTool]:
    """Build Agent-callable capabilities for the current request runtime.

    These LangChain tools are intentionally read-only. They consume existing
    database/cache state and never refresh sub2api or write analysis results.
    """

    @_agent_capability("api_pool_status.get")
    async def api_pool_status_get(pool_id: str) -> dict[str, Any]:
        """Read existing cached API pool capacity status by pool id. This never refreshes sub2api."""
        return await read_pool_capacity(db, pool_id)

    @_agent_capability("account_probe.get")
    async def account_probe_get(site_id: str, group_id: int, account_type: str | None = None) -> dict[str, Any]:
        """Read existing account probe summary by site and group. This never starts a new probe run."""
        return await read_probe_summary(db, site_id=site_id, group_id=group_id, account_type=account_type)

    @_agent_capability("refill_decision.calculate")
    def refill_decision_calculate(pool: dict[str, Any], capacity: dict[str, Any], probe: dict[str, Any]) -> dict[str, Any]:
        """Calculate deterministic refill and warning advice from prepared read-only data."""
        return decide_pool_action(pool=pool, capacity=capacity, probe=probe)

    return {
        api_pool_status_get.name: api_pool_status_get,
        account_probe_get.name: account_probe_get,
        refill_decision_calculate.name: refill_decision_calculate,
    }


async def invoke_agent_capability(db: AsyncIOMotorDatabase, name: str, arguments: dict[str, Any]) -> Any:
    capabilities = build_read_only_agent_capabilities(db)
    capability = capabilities.get(name)
    if capability is None:
        raise ValueError(f"Unknown Agent capability: {name}")
    return await capability.ainvoke(arguments)


def capability_manifest() -> list[dict[str, Any]]:
    return [
        {
            "name": "api_pool_status.get",
            "kind": "read_only",
            "implemented_as": "langchain_tool",
            "description": "Read existing cached API pool capacity status. It never refreshes sub2api.",
        },
        {
            "name": "account_probe.get",
            "kind": "read_only",
            "implemented_as": "langchain_tool",
            "description": "Read existing account probe summary. It never starts a new probe run.",
        },
        {
            "name": "refill_decision.calculate",
            "kind": "read_only_decision",
            "implemented_as": "langchain_tool",
            "description": "Calculate deterministic refill and warning advice from read-only data.",
        },
    ]
