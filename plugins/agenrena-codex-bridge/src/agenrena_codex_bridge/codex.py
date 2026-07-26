from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping, Optional, Sequence

from .media import MaterializedMedia
from .models import CodexTurnResult, IncomingMessage


LOGGER = logging.getLogger(__name__)

CLIENT_INFO = {
    "name": "agenrena-codex-bridge",
    "title": "Agenrena Codex Bridge",
    "version": "0.3.0",
}

OPT_OUT_NOTIFICATIONS = [
    "account/rateLimits/updated",
    "command/exec/outputDelta",
    "item/commandExecution/outputDelta",
    "item/commandExecution/terminalInteraction",
    "item/fileChange/outputDelta",
    "item/plan/delta",
    "item/reasoning/summaryPartAdded",
    "item/reasoning/summaryTextDelta",
    "item/reasoning/textDelta",
    "mcpServer/startupStatus/updated",
    "thread/status/changed",
    "thread/tokenUsage/updated",
]

NotificationHandler = Callable[[Mapping[str, Any]], None]
ServerRequestHandler = Callable[[Mapping[str, Any]], Awaitable[Any]]


class CodexProtocolError(RuntimeError):
    pass


def _sender_metadata_input(
    message: IncomingMessage,
) -> Optional[dict[str, Any]]:
    if not message.sender_id:
        return None

    serialized = json.dumps(
        {"id": message.sender_id},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return {
        "type": "text",
        "text": f"Agenrena sender: {serialized}",
        "text_elements": [],
    }


def _sandbox_policy(mode: str) -> Mapping[str, str]:
    normalized = mode.strip().lower()
    if normalized == "read-only":
        return {"type": "readOnly"}
    if normalized == "workspace-write":
        return {"type": "workspaceWrite"}
    if normalized == "danger-full-access":
        return {"type": "dangerFullAccess"}
    raise CodexProtocolError(f"Unsupported Codex sandbox mode: {mode}")


class AppServerClient:
    def __init__(
        self,
        *,
        command: Sequence[str],
        cwd: Path,
        on_notification: Optional[NotificationHandler] = None,
        on_server_request: Optional[ServerRequestHandler] = None,
    ):
        self.command = tuple(command)
        self.cwd = cwd
        self.on_notification = on_notification
        self.on_server_request = on_server_request
        self.process: Optional[asyncio.subprocess.Process] = None
        self._next_request_id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader_task: Optional[asyncio.Task[None]] = None
        self._stderr_task: Optional[asyncio.Task[None]] = None
        self._stderr = ""
        self._closing = False
        self.closed: Optional[asyncio.Future[None]] = None

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self.closed = loop.create_future()
        self.process = await asyncio.create_subprocess_exec(
            *self.command,
            cwd=str(self.cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._read_stderr())

    async def request(self, method: str, params: Mapping[str, Any]) -> Any:
        if self.process is None:
            raise CodexProtocolError("Codex app-server has not been started.")
        self._next_request_id += 1
        request_id = self._next_request_id
        future = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        try:
            await self._write({"id": request_id, "method": method, "params": params})
            return await future
        finally:
            self._pending.pop(request_id, None)

    async def respond(self, request_id: Any, result: Any) -> None:
        await self._write({"id": request_id, "result": result})

    async def close(self) -> None:
        process = self.process
        if process is None:
            return

        self._closing = True
        if process.stdin and not process.stdin.is_closing():
            process.stdin.close()
            try:
                await process.stdin.wait_closed()
            except (BrokenPipeError, ConnectionResetError):
                pass

        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()

        current = asyncio.current_task()
        for task in (self._reader_task, self._stderr_task):
            if task and task is not current and not task.done():
                task.cancel()
        for task in (self._reader_task, self._stderr_task):
            if task and task is not current:
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        self.process = None

    @property
    def stderr(self) -> str:
        return self._stderr.strip()

    async def _write(self, payload: Mapping[str, Any]) -> None:
        if (
            self.process is None
            or self.process.stdin is None
            or self.process.stdin.is_closing()
        ):
            raise CodexProtocolError("Codex app-server stdin is closed.")
        raw = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        self.process.stdin.write(raw)
        await self.process.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self.process is not None
        assert self.process.stdout is not None
        try:
            while True:
                raw = await self.process.stdout.readline()
                if not raw:
                    break
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    LOGGER.warning("Ignored a non-JSON line from Codex app-server.")
                    continue
                if isinstance(message, Mapping):
                    await self._dispatch(message)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._reject_pending(error)
        finally:
            return_code = await self.process.wait()
            if self._closing:
                if self.closed is not None and not self.closed.done():
                    self.closed.set_result(None)
            else:
                error = CodexProtocolError(
                    self.stderr
                    or f"Codex app-server exited unexpectedly with code {return_code}."
                )
                self._reject_pending(error)
                if self.closed is not None and not self.closed.done():
                    self.closed.set_exception(error)

    async def _read_stderr(self) -> None:
        assert self.process is not None
        assert self.process.stderr is not None
        while True:
            raw = await self.process.stderr.read(4096)
            if not raw:
                return
            self._stderr = (self._stderr + raw.decode("utf-8", errors="replace"))[-20000:]

    async def _dispatch(self, message: Mapping[str, Any]) -> None:
        if "method" in message and "id" in message:
            asyncio.create_task(self._handle_server_request(message))
            return

        if "id" in message:
            request_id = message.get("id")
            pending = self._pending.get(request_id)
            if pending is None or pending.done():
                return
            error = message.get("error")
            if isinstance(error, Mapping):
                pending.set_exception(
                    CodexProtocolError(str(error.get("message") or "Codex request failed."))
                )
            else:
                pending.set_result(message.get("result"))
            return

        if "method" in message and self.on_notification is not None:
            self.on_notification(message)

    async def _handle_server_request(self, message: Mapping[str, Any]) -> None:
        request_id = message.get("id")
        try:
            if self.on_server_request is None:
                result = "decline"
            else:
                result = await self.on_server_request(message)
            await self.respond(request_id, result)
        except Exception as error:
            LOGGER.error(
                "Failed to answer Codex server request %s: %s",
                request_id,
                error,
            )

    def _reject_pending(self, error: Exception) -> None:
        for pending in self._pending.values():
            if not pending.done():
                pending.set_exception(error)


async def decline_server_request(message: Mapping[str, Any]) -> Any:
    method = str(message.get("method") or "")
    if method == "item/permissions/requestApproval":
        return {"permissions": {}, "scope": "turn"}
    return {"decision": "decline"}


class CodexRunner:
    def __init__(
        self,
        *,
        codex_bin: str,
        workspace: Path,
        model: Optional[str] = None,
        sandbox_mode: str = "read-only",
        approval_policy: str = "never",
        timeout_seconds: int = 900,
        command_override: Optional[Sequence[str]] = None,
    ):
        self.codex_bin = codex_bin
        self.workspace = workspace
        self.model = model
        self.sandbox_mode = sandbox_mode
        self.approval_policy = approval_policy
        self.timeout_seconds = timeout_seconds
        self.command_override = tuple(command_override) if command_override else None

    def _command(self) -> Sequence[str]:
        if self.command_override is not None:
            return self.command_override
        command = [
            self.codex_bin,
            "app-server",
            "-c",
            f"approval_policy={json.dumps(self.approval_policy)}",
            "-c",
            f"sandbox_mode={json.dumps(self.sandbox_mode)}",
        ]
        if self.model:
            command.extend(["-c", f"model={json.dumps(self.model)}"])
        return command

    async def run_turn(
        self,
        *,
        message: IncomingMessage,
        thread_id: Optional[str],
        media: Sequence[MaterializedMedia] = (),
    ) -> CodexTurnResult:
        active_turn_id: Optional[str] = None
        final_reply = ""
        fallback_reply = ""
        agent_messages: dict[str, dict[str, Optional[str]]] = {}
        early_notifications: list[Mapping[str, Any]] = []
        completion = asyncio.get_running_loop().create_future()

        def on_notification(notification: Mapping[str, Any]) -> None:
            nonlocal final_reply, fallback_reply
            if active_turn_id is None:
                early_notifications.append(notification)
                return
            method = notification.get("method")
            params = notification.get("params")
            if not isinstance(params, Mapping):
                return

            if method == "item/started" and params.get("turnId") == active_turn_id:
                item = params.get("item")
                if isinstance(item, Mapping) and item.get("type") == "agentMessage":
                    agent_messages[str(item.get("id"))] = {
                        "phase": str(item.get("phase") or "") or None,
                        "text": str(item.get("text") or ""),
                    }
                return

            if method == "item/agentMessage/delta" and params.get("turnId") == active_turn_id:
                item_id = str(params.get("itemId") or "")
                current = agent_messages.setdefault(
                    item_id, {"phase": None, "text": ""}
                )
                current["text"] = str(current.get("text") or "") + str(
                    params.get("delta") or ""
                )
                return

            if method == "item/completed" and params.get("turnId") == active_turn_id:
                item = params.get("item")
                if not isinstance(item, Mapping) or item.get("type") != "agentMessage":
                    return
                item_id = str(item.get("id") or "")
                current = agent_messages.get(item_id, {"phase": None, "text": ""})
                phase = str(item.get("phase") or current.get("phase") or "") or None
                text = str(item.get("text") or current.get("text") or "").strip()
                agent_messages[item_id] = {"phase": phase, "text": text}
                if text:
                    fallback_reply = text
                    if phase == "final_answer":
                        final_reply = text
                return

            if method == "turn/completed":
                turn = params.get("turn")
                if (
                    isinstance(turn, Mapping)
                    and turn.get("id") == active_turn_id
                    and not completion.done()
                ):
                    completion.set_result(turn)
                return

            if (
                method == "error"
                and params.get("turnId") == active_turn_id
                and not params.get("willRetry")
                and not completion.done()
            ):
                error = params.get("error")
                if isinstance(error, Mapping):
                    detail = str(error.get("message") or "Codex turn failed.")
                else:
                    detail = str(error or "Codex turn failed.")
                completion.set_exception(CodexProtocolError(detail))

        client = AppServerClient(
            command=self._command(),
            cwd=self.workspace,
            on_notification=on_notification,
            on_server_request=decline_server_request,
        )
        await client.start()
        try:
            await client.request(
                "initialize",
                {
                    "clientInfo": CLIENT_INFO,
                    "capabilities": {
                        "experimentalApi": False,
                        "optOutNotificationMethods": OPT_OUT_NOTIFICATIONS,
                    },
                },
            )

            thread_params: dict[str, Any] = {
                "cwd": str(self.workspace),
                "approvalPolicy": self.approval_policy,
            }
            if self.model:
                thread_params["model"] = self.model

            if thread_id:
                thread_params["threadId"] = thread_id
                thread_result = await client.request("thread/resume", thread_params)
            else:
                thread_result = await client.request("thread/start", thread_params)

            resolved_thread_id = str(thread_result["thread"]["id"])
            turn_input: list[dict[str, Any]] = []
            sender_metadata = _sender_metadata_input(message)
            if sender_metadata is not None:
                turn_input.append(sender_metadata)
            if message.text:
                turn_input.append(
                    {
                        "type": "text",
                        "text": message.text,
                        "text_elements": [],
                    }
                )
            for item in media:
                if item.kind == "sticker":
                    turn_input.append(
                        {
                            "type": "text",
                            "text": "The user sent the following sticker.",
                            "text_elements": [],
                        }
                    )
                turn_input.append(
                    {
                        "type": "localImage",
                        "path": str(item.path),
                    }
                )
            if not turn_input:
                raise CodexProtocolError("A Codex turn requires text or media input.")

            turn_params: dict[str, Any] = {
                "threadId": resolved_thread_id,
                "input": turn_input,
                "cwd": str(self.workspace),
                "approvalPolicy": self.approval_policy,
                "sandboxPolicy": _sandbox_policy(self.sandbox_mode),
                "clientUserMessageId": message.message_id,
            }
            if self.model:
                turn_params["model"] = self.model

            turn_result = await client.request("turn/start", turn_params)
            active_turn_id = str(turn_result["turn"]["id"])
            buffered_notifications = list(early_notifications)
            early_notifications.clear()
            for notification in buffered_notifications:
                on_notification(notification)

            assert client.closed is not None
            done, _ = await asyncio.wait(
                {completion, client.closed},
                timeout=self.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if not done:
                raise asyncio.TimeoutError(
                    f"Codex turn exceeded {self.timeout_seconds} seconds."
                )
            if client.closed in done:
                await client.closed

            turn = await completion
            status = str(turn.get("status") or "")
            if status not in {"completed", "success"}:
                error = turn.get("error")
                raise CodexProtocolError(
                    str(error or f"Codex turn ended with status {status or 'unknown'}.")
                )

            reply_text = (final_reply or fallback_reply).strip()
            if not reply_text:
                raise CodexProtocolError("Codex completed without a final message.")
            return CodexTurnResult(
                thread_id=resolved_thread_id,
                turn_id=active_turn_id,
                reply_text=reply_text,
            )
        finally:
            await client.close()
