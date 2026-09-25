from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from typing import Any

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from .contracts import ContractError, Contracts

logger = logging.getLogger(__name__)


class EvidenceGateway:
    def __init__(
        self,
        endpoint_or_session: str | ClientSession,
        contracts: Contracts,
        team_api_key: str | None = None,
    ) -> None:
        self._contracts = contracts
        if isinstance(endpoint_or_session, ClientSession):
            self._session: ClientSession | None = endpoint_or_session
            self._endpoint: str | None = None
            self._team_api_key: str | None = team_api_key
        else:
            self._session = None
            self._endpoint = endpoint_or_session
            self._team_api_key = team_api_key
        self._stack: AsyncExitStack | None = None

    async def connect(self) -> None:
        if self._endpoint is None or self._team_api_key is None:
            return
        if self._stack is not None:
            await self.close()
        stack = AsyncExitStack()
        try:
            headers = {"Authorization": f"Bearer {self._team_api_key}"}
            timeout = httpx2.Timeout(300.0, connect=30.0, write=30.0, read=300.0, pool=30.0)
            http_client = await stack.enter_async_context(
                httpx2.AsyncClient(headers=headers, timeout=timeout)
            )
            read_stream, write_stream = await stack.enter_async_context(
                streamable_http_client(self._endpoint, http_client=http_client)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
            self._session = session
            self._stack = stack
        except Exception:
            await stack.aclose()
            raise

    async def close(self) -> None:
        if self._stack is not None:
            with suppress(Exception):
                await self._stack.aclose()
            self._stack = None
            self._session = None

    async def list_tools(self) -> list[str]:
        if self._session is None:
            await self.connect()
        assert self._session is not None
        response = await self._session.list_tools()
        return sorted(tool.name for tool in response.tools)

    async def call(self, tool_name: str, *, case_id: str, **arguments: str) -> dict[str, Any]:
        payload = {"case_id": case_id, **arguments}

        for attempt in range(2):
            try:
                if self._session is None:
                    await self.connect()
                assert self._session is not None
                result = await self._session.call_tool(tool_name, arguments=payload)
                break
            except Exception as exc:
                is_contract_err = isinstance(exc, (ValueError, ContractError))
                if attempt == 0 and self._endpoint is not None and not is_contract_err:
                    logger.info("Reconnecting MCP session after error: %s", exc)
                    with suppress(Exception):
                        await self.connect()
                else:
                    raise

        is_err = getattr(result, "is_error", None)
        if is_err is None:
            is_err = getattr(result, "isError", False)
        if is_err:
            message = " ".join(
                block.text for block in result.content if getattr(block, "text", None)
            )
            raise RuntimeError(f"MCP tool {tool_name} failed: {message or 'unknown error'}")

        evidence = getattr(result, "structuredContent", None)
        if evidence is None:
            evidence = getattr(result, "structured_content", None)
        if evidence is None:
            text_blocks = [block.text for block in result.content if getattr(block, "text", None)]
            if len(text_blocks) != 1:
                raise ValueError(f"MCP tool {tool_name} did not return one evidence object")
            evidence = json.loads(text_blocks[0])

        self._contracts.validate_evidence(evidence, f"MCP tool {tool_name}")
        return evidence


@asynccontextmanager
async def connect_gateway(
    endpoint: str, team_api_key: str, contracts: Contracts
) -> AsyncIterator[EvidenceGateway]:
    gateway = EvidenceGateway(endpoint, contracts, team_api_key=team_api_key)
    await gateway.connect()
    try:
        yield gateway
    finally:
        await gateway.close()
