from __future__ import annotations

import os
from dataclasses import dataclass

import anyio

from ..context import RunContext
from ..logging import get_logger
from ..model import ResumeToken
from ..runner_bridge import (
    ExecBridgeConfig,
    IncomingMessage,
    RunningTasks,
    handle_message,
)
from ..transport import RenderedMessage
from ..transport_runtime import TransportRuntime
from .client import XMPPClient

logger = get_logger(__name__)


@dataclass
class XMPPBridgeConfig:
    client: XMPPClient
    runtime: TransportRuntime
    default_chat: str | None
    startup_msg: str
    exec_cfg: ExecBridgeConfig


async def run_main_loop(
    cfg: XMPPBridgeConfig,
    *,
    default_engine_override: str | None = None,
) -> None:
    """Main XMPP bridge event loop."""
    client = cfg.client
    running_tasks: RunningTasks = {}

    client.start()
    await client.wait_ready()

    if cfg.default_chat:
        client.send_chat(cfg.default_chat, cfg.startup_msg)
        logger.info("xmpp.startup_sent", chat=cfg.default_chat)

    logger.info("xmpp.ready", jid=client.boundjid.bare)

    async with anyio.create_task_group() as tg:
        while True:
            try:
                from_jid, body, msg_id = await client.get_incoming()
            except Exception as e:
                logger.error("xmpp.receive_error", error=str(e))
                await anyio.sleep(1)
                continue

            logger.info(
                "xmpp.incoming",
                from_jid=from_jid,
                msg_id=msg_id,
                text=body[:100] if body else None,
            )

            # Check for cancel command
            if body.strip().lower() in ("/cancel", "cancel"):
                cancelled = _cancel_tasks_for_jid(running_tasks, from_jid)
                if cancelled:
                    client.send_chat(from_jid, f"Cancelled {cancelled} task(s)")
                else:
                    client.send_chat(from_jid, "No running tasks to cancel")
                continue

            # Resolve message context
            resolved = cfg.runtime.resolve_message(
                text=body,
                reply_text=None,
                ambient_context=None,
            )

            # Resolve runner
            resolved_runner = cfg.runtime.resolve_runner(
                resume_token=resolved.resume_token,
                engine_override=resolved.engine_override,
            )

            if not resolved_runner.available:
                issue = resolved_runner.issue or "Engine not available"
                client.send_chat(from_jid, f"Error: {issue}")
                continue

            # Resolve working directory
            run_cwd = cfg.runtime.resolve_run_cwd(resolved.context)

            # Create incoming message
            incoming = IncomingMessage(
                channel_id=from_jid,
                message_id=msg_id or f"xmpp-{id(body)}",
                text=resolved.prompt,
                reply_to=None,
                thread_id=None,
            )

            # Format context line for progress display
            context_line = cfg.runtime.format_context_line(resolved.context)

            # Spawn task to handle message
            tg.start_soon(
                _handle_message_task,
                cfg,
                incoming,
                resolved_runner.runner,
                resolved.resume_token,
                resolved.context,
                context_line,
                run_cwd,
                running_tasks,
            )


async def _handle_message_task(
    cfg: XMPPBridgeConfig,
    incoming: IncomingMessage,
    runner,
    resume_token: ResumeToken | None,
    context: RunContext | None,
    context_line: str | None,
    run_cwd,
    running_tasks: RunningTasks,
) -> None:
    """Handle a single message in a task."""
    original_cwd = os.getcwd() if run_cwd else None
    if run_cwd:
        os.chdir(run_cwd)

    try:
        await handle_message(
            cfg.exec_cfg,
            runner=runner,
            incoming=incoming,
            resume_token=resume_token,
            context=context,
            context_line=context_line,
            running_tasks=running_tasks,
        )
    except Exception as e:
        logger.exception("xmpp.handle_error", error=str(e))
        try:
            await cfg.exec_cfg.transport.send(
                channel_id=incoming.channel_id,
                message=RenderedMessage(text=f"Error: {e}"),
            )
        except Exception:
            pass
    finally:
        if original_cwd:
            os.chdir(original_cwd)


def _cancel_tasks_for_jid(running_tasks: RunningTasks, jid: str) -> int:
    """Cancel all running tasks for a JID. Returns count cancelled."""
    cancelled = 0
    for ref, task in running_tasks.items():
        if ref.channel_id == jid:
            task.cancel_requested.set()
            cancelled += 1
    return cancelled
