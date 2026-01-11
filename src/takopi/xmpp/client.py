from __future__ import annotations

import asyncio
import re
from typing import Any

import slixmpp
from slixmpp.xmlstream import ET

from ..transport import ChannelId, MessageRef, RenderedMessage, SendOptions, Transport


class XMPPClient(slixmpp.ClientXMPP):
    """Slixmpp client wrapper for takopi."""

    def __init__(
        self,
        jid: str,
        password: str,
        *,
        host: str | None = None,
        port: int = 5222,
        use_tls: bool = False,
        allowed_jids: frozenset[str] | None = None,
    ) -> None:
        super().__init__(jid, password)
        self.allowed_jids = allowed_jids
        self._host = host
        self._port = port
        self._use_tls = use_tls
        self._message_queue: asyncio.Queue[tuple[str, str, str | None]] = asyncio.Queue()
        self._session_ready = asyncio.Event()

        self.register_plugin("xep_0199")  # Ping
        self.register_plugin("xep_0085")  # Chat states
        self.register_plugin("xep_0280")  # Carbons

        self.add_event_handler("session_start", self._on_session_start)
        self.add_event_handler("message", self._on_message)

    def start(self) -> None:
        """Connect to the XMPP server."""
        if not self._use_tls:
            self.enable_plaintext = True
            self.enable_starttls = False
            self.enable_direct_tls = False
            self["feature_mechanisms"].unencrypted_plain = True
        self.connect(host=self._host, port=self._port)

    async def _on_session_start(self, event: Any) -> None:
        await self.get_roster()
        self.send_presence()
        self._session_ready.set()

    async def wait_ready(self) -> None:
        """Wait for XMPP session to be established."""
        await self._session_ready.wait()

    async def _on_message(self, msg: slixmpp.Message) -> None:
        if msg["type"] not in ("chat", "normal"):
            return

        body = msg["body"]
        if not body:
            return

        from_jid = str(msg["from"].bare)

        # Check allowlist if configured
        if self.allowed_jids and from_jid not in self.allowed_jids:
            return

        msg_id = msg["id"] or None
        await self._message_queue.put((from_jid, body, msg_id))

    async def get_incoming(self) -> tuple[str, str, str | None]:
        """Get next incoming message (jid, body, msg_id)."""
        return await self._message_queue.get()

    def send_chat(
        self,
        to_jid: str,
        body: str,
        *,
        reply_to_id: str | None = None,
    ) -> str | None:
        """Send a chat message and return the message ID."""
        msg = self.make_message(mto=to_jid, mbody=body, mtype="chat")

        # XEP-0461 reply if supported
        if reply_to_id:
            reply = ET.Element("{urn:xmpp:reply:0}reply")
            reply.set("to", to_jid)
            reply.set("id", reply_to_id)
            msg.xml.append(reply)

        msg.send()
        return msg["id"]


class XMPPTransport(Transport):
    """Transport implementation for XMPP."""

    def __init__(self, client: XMPPClient) -> None:
        self._client = client

    async def close(self) -> None:
        self._client.disconnect()

    async def send(
        self,
        *,
        channel_id: ChannelId,
        message: RenderedMessage,
        options: SendOptions | None = None,
    ) -> MessageRef | None:
        reply_to_id = None
        if options and options.reply_to:
            reply_to_id = str(options.reply_to.message_id)

        # XMPP doesn't support markdown - strip it
        text = _strip_markdown(message.text)

        # Split long messages (XMPP has practical limits around 64KB but
        # mobile clients work better with shorter messages)
        max_len = 3500
        chunks = _split_message(text, max_len)

        last_msg_id = None
        for chunk in chunks:
            last_msg_id = self._client.send_chat(
                str(channel_id),
                chunk,
                reply_to_id=reply_to_id,
            )
            reply_to_id = None  # Only first chunk replies

        if last_msg_id:
            return MessageRef(channel_id=channel_id, message_id=last_msg_id)
        return None

    async def edit(
        self,
        *,
        ref: MessageRef,
        message: RenderedMessage,
        wait: bool = True,
    ) -> MessageRef | None:
        # XMPP message editing (XEP-0308) is not widely supported
        # For now, just send a new message
        return await self.send(
            channel_id=ref.channel_id,
            message=message,
        )

    async def delete(self, *, ref: MessageRef) -> bool:
        # XMPP message retraction (XEP-0424) is not widely supported
        # No-op for now
        return False


_MARKDOWN_PATTERNS = [
    (re.compile(r"\*\*(.+?)\*\*"), r"\1"),  # bold
    (re.compile(r"\*(.+?)\*"), r"\1"),  # italic
    (re.compile(r"__(.+?)__"), r"\1"),  # bold alt
    (re.compile(r"_(.+?)_"), r"\1"),  # italic alt
    (re.compile(r"\[([^\]]+)\]\(([^)]+)\)"), r"\2"),  # links -> url only
    (re.compile(r"```\w*\n?"), ""),  # code block markers
    (re.compile(r"`([^`]+)`"), r"\1"),  # inline code
]


def _strip_markdown(text: str) -> str:
    """Strip markdown formatting for plain text XMPP clients."""
    for pattern, replacement in _MARKDOWN_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _split_message(text: str, max_len: int) -> list[str]:
    """Split message into chunks, preferring line breaks."""
    if len(text) <= max_len:
        return [text]

    chunks = []
    while text:
        if len(text) <= max_len:
            chunks.append(text)
            break

        # Find a good split point
        split_at = max_len
        newline_pos = text.rfind("\n", 0, max_len)
        if newline_pos > max_len // 2:
            split_at = newline_pos + 1
        else:
            space_pos = text.rfind(" ", 0, max_len)
            if space_pos > max_len // 2:
                split_at = space_pos + 1

        chunks.append(text[:split_at].rstrip())
        text = text[split_at:].lstrip()

    return chunks
