from __future__ import annotations

import os
from pathlib import Path

import anyio

from ..backends import EngineBackend, SetupIssue
from ..config import ConfigError
from ..logging import get_logger
from ..runner_bridge import ExecBridgeConfig
from ..transports import SetupResult, TransportBackend
from ..transport_runtime import TransportRuntime
from .bridge import XMPPBridgeConfig, run_main_loop
from .client import XMPPClient, XMPPTransport
from .presenter import XMPPPresenter

logger = get_logger(__name__)


def _require_xmpp_config(
    transport_config: dict[str, object],
    config_path: Path,
) -> tuple[str, str, int | None]:
    """Extract and validate XMPP config."""
    jid = transport_config.get("jid")
    if not jid or not isinstance(jid, str):
        raise ConfigError(
            f"Missing `transports.xmpp.jid` in {config_path}"
        )

    password = transport_config.get("password")
    if not password or not isinstance(password, str):
        # Try environment variable
        password = os.environ.get("TAKOPI_XMPP_PASSWORD")
        if not password:
            raise ConfigError(
                f"Missing `transports.xmpp.password` in {config_path} "
                "or TAKOPI_XMPP_PASSWORD environment variable"
            )

    chat_id = transport_config.get("chat_id")
    if chat_id is not None and not isinstance(chat_id, str):
        raise ConfigError(
            f"Invalid `transports.xmpp.chat_id` in {config_path}; expected a JID string"
        )

    return jid, password, chat_id


def _build_startup_message(runtime: TransportRuntime, cwd: str) -> str:
    engines = list(runtime.available_engine_ids())
    missing = list(runtime.missing_engine_ids())
    engine_str = ", ".join(engines) if engines else "none"
    if missing:
        engine_str = f"{engine_str} (missing: {', '.join(missing)})"

    projects = sorted(runtime.project_aliases(), key=str.lower)
    project_str = ", ".join(projects) if projects else "none"

    return (
        f"takopi is ready\n\n"
        f"default: {runtime.default_engine}\n"
        f"agents: {engine_str}\n"
        f"projects: {project_str}\n"
        f"cwd: {cwd}"
    )


class XMPPBackend(TransportBackend):
    id = "xmpp"
    description = "XMPP chat (Jabber)"

    def check_setup(
        self,
        engine_backend: EngineBackend,
        *,
        transport_override: str | None = None,
    ) -> SetupResult:
        # Check for slixmpp
        issues: list[SetupIssue] = []
        try:
            import slixmpp  # noqa: F401
        except ImportError:
            issues.append(
                SetupIssue(
                    message="slixmpp not installed",
                    hint="Run: uv add slixmpp",
                )
            )

        config_path = Path.home() / ".config" / "takopi" / "config.toml"
        return SetupResult(issues=issues, config_path=config_path)

    def interactive_setup(self, *, force: bool) -> bool:
        # TODO: implement setup wizard
        print("XMPP setup not yet implemented.")
        print("Add to ~/.config/takopi/config.toml:")
        print()
        print("[transports.xmpp]")
        print('jid = "bot@your-server.com"')
        print('password = "your-password"')
        print('allowed_jids = ["you@your-server.com"]')
        print()
        return False

    def lock_token(
        self,
        *,
        transport_config: dict[str, object],
        config_path: Path,
    ) -> str | None:
        jid, _, _ = _require_xmpp_config(transport_config, config_path)
        return jid

    def build_and_run(
        self,
        *,
        transport_config: dict[str, object],
        config_path: Path,
        runtime: TransportRuntime,
        final_notify: bool,
        default_engine_override: str | None,
    ) -> None:
        jid, password, default_chat = _require_xmpp_config(
            transport_config, config_path
        )

        # Get allowed JIDs
        allowed_raw = transport_config.get("allowed_jids", [])
        if not isinstance(allowed_raw, list):
            raise ConfigError(
                f"Invalid `transports.xmpp.allowed_jids` in {config_path}; "
                "expected a list of JID strings"
            )
        allowed_jids = frozenset(str(j) for j in allowed_raw) if allowed_raw else None

        # Connection options
        host = transport_config.get("host")
        port = int(transport_config.get("port", 5222))
        use_tls = bool(transport_config.get("use_tls", False))

        startup_msg = _build_startup_message(runtime, os.getcwd())

        client = XMPPClient(
            jid,
            password,
            host=host,
            port=port,
            use_tls=use_tls,
            allowed_jids=allowed_jids,
        )
        transport = XMPPTransport(client)
        presenter = XMPPPresenter()

        exec_cfg = ExecBridgeConfig(
            transport=transport,
            presenter=presenter,
            final_notify=final_notify,
        )

        cfg = XMPPBridgeConfig(
            client=client,
            runtime=runtime,
            default_chat=default_chat,
            startup_msg=startup_msg,
            exec_cfg=exec_cfg,
        )

        async def run_loop() -> None:
            await run_main_loop(
                cfg,
                default_engine_override=default_engine_override,
            )

        anyio.run(run_loop)


xmpp_backend = XMPPBackend()
BACKEND = xmpp_backend
