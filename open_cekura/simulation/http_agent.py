"""Windows-safe stdlib HTTP implementation of the agent adapter contract."""

from __future__ import annotations

import asyncio
import json
import socket
from typing import Annotated, Literal, TypeVar
from urllib import error, request
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, JsonValue, StrictBool, ValidationError

from open_cekura.domain.enums import AdapterKind
from open_cekura.domain.models import AgentVersion
from open_cekura.scenarios.schema import ScenarioDefinition

from .agent_adapter import (
    AdapterContractError,
    AdapterSession,
    AdapterStateError,
    AdapterTimeoutError,
    AdapterTransportError,
    AgentAdapter,
    AgentReply,
    ToolCallObservation,
)


_MAX_RESPONSE_BYTES = 1_048_576
_ResponseModel = TypeVar("_ResponseModel", bound=BaseModel)


class _WireContract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class _Acknowledgement(_WireContract):
    schema_version: Literal[1]
    accepted: StrictBool
    initial_state: dict[str, JsonValue] = Field(default_factory=dict)


class _ToolCallsResponse(_WireContract):
    schema_version: Literal[1]
    tool_calls: list[ToolCallObservation]


class HTTPAgentAdapter(AgentAdapter):
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: float,
        max_response_bytes: Annotated[int, Field(gt=0)] = _MAX_RESPONSE_BYTES,
    ):
        if not isinstance(base_url, str):
            raise TypeError("base_url must be a string")
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("base_url must be an absolute http or https URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("base_url must not contain credentials")
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise TypeError("timeout_seconds must be a positive number")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than zero")
        if isinstance(max_response_bytes, bool) or not isinstance(max_response_bytes, int):
            raise TypeError("max_response_bytes must be a positive integer")
        if max_response_bytes <= 0:
            raise ValueError("max_response_bytes must be greater than zero")

        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = float(timeout_seconds)
        self.max_response_bytes = max_response_bytes
        self._started = False
        self._closed = False

    async def start(
        self,
        scenario: ScenarioDefinition,
        agent_version: AgentVersion,
    ) -> AdapterSession:
        if not isinstance(scenario, ScenarioDefinition):
            raise AdapterContractError("scenario must be a validated ScenarioDefinition")
        if not isinstance(agent_version, AgentVersion):
            raise AdapterContractError("agent_version must be a validated AgentVersion")
        if agent_version.adapter_kind is not AdapterKind.HTTP:
            raise AdapterContractError("HTTPAgentAdapter requires adapter_kind=http")
        if self._started and not self._closed:
            raise AdapterStateError("adapter session is already started")

        payload = await self._request_json(
            "POST",
            "/start",
            {
                "schema_version": 1,
                "scenario": scenario.model_dump(mode="json"),
                "agent_version": agent_version.model_dump(mode="json"),
            },
            operation="start",
        )
        acknowledgement = self._validate_response(_Acknowledgement, payload, "start")
        if not acknowledgement.accepted:
            raise AdapterContractError("start response did not accept the session")
        self._started = True
        self._closed = False
        return AdapterSession(initial_state=acknowledgement.initial_state)

    async def send(self, message: str) -> AgentReply:
        self._require_active()
        if not isinstance(message, str) or not message:
            raise AdapterContractError("message must be a non-empty string")
        payload = await self._request_json(
            "POST",
            "/send",
            {"schema_version": 1, "message": message},
            operation="send",
        )
        return self._validate_response(AgentReply, payload, "send")

    async def observe_tool_calls(self) -> tuple[ToolCallObservation, ...]:
        self._require_active()
        payload = await self._request_json(
            "GET",
            "/tool-calls",
            None,
            operation="tool-calls",
        )
        response = self._validate_response(
            _ToolCallsResponse,
            payload,
            "tool-calls",
        )
        return tuple(response.tool_calls)

    async def close(self) -> None:
        if not self._started or self._closed:
            self._closed = True
            return
        payload = await self._request_json(
            "POST",
            "/close",
            {"schema_version": 1},
            operation="close",
        )
        acknowledgement = self._validate_response(_Acknowledgement, payload, "close")
        if not acknowledgement.accepted:
            raise AdapterContractError("close response did not accept the request")
        self._closed = True

    def _require_active(self) -> None:
        if not self._started:
            raise AdapterStateError("adapter session has not been started")
        if self._closed:
            raise AdapterStateError("adapter session is closed")

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        *,
        operation: str,
    ) -> dict[str, object]:
        return await asyncio.to_thread(
            self._request_json_sync,
            method,
            path,
            payload,
            operation,
        )

    def _request_json_sync(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None,
        operation: str,
    ) -> dict[str, object]:
        body = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            body = json.dumps(
                payload,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
            headers["Content-Type"] = "application/json; charset=utf-8"
        http_request = request.Request(
            f"{self.base_url}{path}",
            data=body,
            headers=headers,
            method=method,
        )
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                response_body = response.read(self.max_response_bytes + 1)
        except (TimeoutError, socket.timeout) as exc:
            raise AdapterTimeoutError(
                f"{operation} request timed out after {self.timeout_seconds:g}s"
            ) from exc
        except error.HTTPError as exc:
            raise AdapterTransportError(
                f"{operation} request returned HTTP {exc.code}"
            ) from exc
        except error.URLError as exc:
            if isinstance(exc.reason, (TimeoutError, socket.timeout)):
                raise AdapterTimeoutError(
                    f"{operation} request timed out after {self.timeout_seconds:g}s"
                ) from exc
            raise AdapterTransportError(f"{operation} request failed: {exc.reason}") from exc
        except OSError as exc:
            raise AdapterTransportError(f"{operation} request failed: {exc}") from exc

        if len(response_body) > self.max_response_bytes:
            raise AdapterContractError(
                f"{operation} response exceeded {self.max_response_bytes} bytes"
            )
        try:
            decoded = json.loads(response_body)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AdapterContractError(
                f"{operation} response was not valid UTF-8 JSON"
            ) from exc
        if not isinstance(decoded, dict):
            raise AdapterContractError(f"{operation} response must be a JSON object")
        return decoded

    @staticmethod
    def _validate_response(
        model: type[_ResponseModel],
        payload: dict[str, object],
        operation: str,
    ) -> _ResponseModel:
        try:
            return model.model_validate(payload)
        except ValidationError as exc:
            raise AdapterContractError(
                f"{operation} response contract validation failed: {exc}"
            ) from exc


__all__ = ["HTTPAgentAdapter"]
