from __future__ import annotations

import asyncio
import logging
import signal
import sys

from .agenrena import AgenrenaAPIClient, AgenrenaWebSocketClient
from .bridge import BridgeService
from .codex import CodexRunner
from .config import ConfigurationError, Settings
from .media import MediaStore
from .state import StateStore


LOGGER = logging.getLogger(__name__)


async def run(settings: Settings) -> None:
    state_store = StateStore(settings.state_dir / "bridge-state.json")
    media_store = MediaStore(settings.state_dir / "media")
    await media_store.prepare()
    service = BridgeService(
        message_source=AgenrenaWebSocketClient(
            ws_url=settings.ws_url,
            api_key=settings.api_key,
        ),
        reply_client=AgenrenaAPIClient(
            api_base=settings.api_base,
            api_key=settings.api_key,
            user_agent=settings.user_agent,
        ),
        codex_runner=CodexRunner(
            codex_bin=settings.codex_bin,
            workspace=settings.codex_workspace,
            model=settings.codex_model,
            sandbox_mode=settings.codex_sandbox_mode,
            approval_policy=settings.codex_approval_policy,
            timeout_seconds=settings.codex_turn_timeout_seconds,
        ),
        state_store=state_store,
        media_store=media_store,
    )

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()
    for signal_name in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signal_name, stop_event.set)
        except NotImplementedError:
            pass

    service_task = asyncio.create_task(service.run())
    stop_task = asyncio.create_task(stop_event.wait())
    done, _ = await asyncio.wait(
        {service_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
    )

    if service_task in done:
        stop_task.cancel()
        await service_task
        return

    LOGGER.info("Stopping Agenrena Codex Bridge.")
    service_task.cancel()
    try:
        await service_task
    except asyncio.CancelledError:
        pass


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigurationError as error:
        print(f"configuration error: {error}", file=sys.stderr)
        raise SystemExit(2)

    logging.basicConfig(
        level=getattr(logging, settings.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    LOGGER.info(
        "Starting Agenrena Codex Bridge (workspace=%s, sandbox=%s).",
        settings.codex_workspace,
        settings.codex_sandbox_mode,
    )
    try:
        asyncio.run(run(settings))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
