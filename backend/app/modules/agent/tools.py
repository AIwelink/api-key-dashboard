from __future__ import annotations

from typing import Any

from app.modules.agent.capabilities import capability_manifest
from app.modules.agent.langchain_adapter import langchain_available
from app.modules.agent.llm import level1_config


def tool_manifest() -> dict[str, Any]:
    framework_available = langchain_available()
    llm = level1_config()
    return {
        "framework": "langchain",
        "available": framework_available,
        "mode": "read_only_mvp",
        "orchestration": "langchain_chain" if framework_available else "http_fallback",
        "llm": {**llm, "framework": "langchain" if framework_available else "http_fallback"},
        "product_term": "Agent callable capabilities",
        "capabilities": capability_manifest(),
        "tools": capability_manifest(),
    }
