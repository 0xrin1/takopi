from __future__ import annotations

from ..presenter import Presenter
from ..progress import ProgressState
from ..transport import RenderedMessage


class XMPPPresenter(Presenter):
    """Plain text presenter for XMPP clients that don't support markdown."""

    def render_progress(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        label: str = "working",
    ) -> RenderedMessage:
        parts = []

        # Status line
        elapsed_str = _format_elapsed(elapsed_s)
        parts.append(f"[{label}] {elapsed_str}")

        # Context line if present
        if state.context_line:
            parts.append(state.context_line)

        # Action count
        if state.action_count > 0:
            parts.append(f"actions: {state.action_count}")

        # Current tool if any
        if state.current_tool:
            parts.append(f"tool: {state.current_tool}")

        # Cost if tracked
        if state.cost_usd is not None and state.cost_usd > 0:
            parts.append(f"cost: ${state.cost_usd:.4f}")

        return RenderedMessage(text="\n".join(parts))

    def render_final(
        self,
        state: ProgressState,
        *,
        elapsed_s: float,
        status: str,
        answer: str,
    ) -> RenderedMessage:
        parts = []

        # Header with status
        elapsed_str = _format_elapsed(elapsed_s)
        status_icon = "done" if status == "done" else "error"
        parts.append(f"[{status_icon}] {elapsed_str}")

        # Stats line
        stats = []
        if state.action_count > 0:
            stats.append(f"{state.action_count} actions")
        if state.cost_usd is not None and state.cost_usd > 0:
            stats.append(f"${state.cost_usd:.4f}")
        if stats:
            parts.append(" | ".join(stats))

        # Resume token if present
        if state.resume_line:
            parts.append(state.resume_line)

        # Separator and answer
        if answer.strip():
            parts.append("")
            parts.append(answer.strip())

        return RenderedMessage(text="\n".join(parts))


def _format_elapsed(seconds: float) -> str:
    """Format elapsed time as human readable."""
    if seconds < 60:
        return f"{seconds:.0f}s"
    minutes = int(seconds // 60)
    secs = int(seconds % 60)
    if minutes < 60:
        return f"{minutes}m {secs}s"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m"
