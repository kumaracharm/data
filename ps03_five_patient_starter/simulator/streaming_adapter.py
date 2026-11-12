from __future__ import annotations

import asyncio
import json
import urllib.request
from collections.abc import AsyncIterable, Awaitable, Callable
from typing import Any


FORBIDDEN_OBSERVED_KEYS = {
    "true_hr_bpm",
    "true_spo2_percent",
    "true_rr_bpm",
    "true_temp_c",
    "scenario_name",
    "scenario_event_id",
    "scenario_event_active",
    "event_onset_timestamp",
    "event_end_timestamp",
    "injected_physiological_changes_json",
    "sensor_fault_label",
}


class StreamingAdapter:
    """Temporary JSON transport; gateway URL and observation contract remain configurable."""

    def __init__(
        self,
        gateway_url: str | None = None,
        sink: Callable[[dict[str, Any]], Awaitable[None]] | None = None,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.gateway_url = gateway_url
        self.sink = sink
        self.timeout_seconds = timeout_seconds

    async def publish(self, observation: dict[str, Any]) -> None:
        leaked = FORBIDDEN_OBSERVED_KEYS.intersection(observation)
        if leaked:
            raise ValueError(f"Ground-truth fields are forbidden in observed payloads: {sorted(leaked)}")
        if self.sink is not None:
            await self.sink(dict(observation))
        elif self.gateway_url:
            await asyncio.to_thread(self._post_json, observation)
        else:
            raise RuntimeError("Configure a gateway_url or a sink before publishing observations.")

    def _post_json(self, observation: dict[str, Any]) -> None:
        payload = json.dumps(observation).encode("utf-8")
        request = urllib.request.Request(
            self.gateway_url or "",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            if response.status < 200 or response.status >= 300:
                raise RuntimeError(f"Gateway returned HTTP {response.status}")

    async def forward(self, observations: AsyncIterable[dict[str, Any]]) -> None:
        async for observation in observations:
            await self.publish(observation)
