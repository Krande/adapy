"""Waiting for a viewer page to come live on the websocket, and saying why when it does not."""

import asyncio
import time

from playwright.async_api import Page

from ada.comms.wsock.client_async import WebSocketClientAsync


def capture_console(page: Page) -> list[str]:
    """The page's console messages and errors from now on (attach before ``goto``): what a viewer that
    never came live printed is what tells a CI log why."""
    lines: list[str] = []
    page.on("console", lambda msg: lines.append(f"[{msg.type}] {msg.text}"))
    page.on("pageerror", lambda exc: lines.append(f"[pageerror] {exc}"))
    return lines


async def wait_until_live(
    ws_client: WebSocketClientAsync,
    what: str = "the viewer",
    target_id=None,
    console: list[str] | None = None,
    seconds: float = 30.0,
) -> None:
    """Ask until the viewer answers a liveness PING, for ``seconds`` of wall clock in all.

    The page has to boot, open its websocket and register as a WEB target before there is anything to
    answer, which on a loaded runner takes seconds; each ask waits up to a second for the PONG.
    """
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if await ws_client.check_target_liveness(target_id=target_id):
            return
        await asyncio.sleep(0.2)
    tail = "\n".join((console or [])[-40:]) or "(no console output captured)"
    raise AssertionError(f"{what} never became live on the websocket within {seconds:.0f} s; page console:\n{tail}")
