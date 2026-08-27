"""UPS status command and the mute buttons on UPS alerts."""

import logging
import time
from datetime import timedelta
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from aiogram.enums import ChatAction
from aiogram.types import CallbackQuery, Message

from src.nut.client import STATUS_MEANINGS
from src.nut.monitor import format_duration, format_runtime, parse_status
from src.utils.formatting import format_mute_expiry, safe_reply, truncate_message

if TYPE_CHECKING:
    from src.alerts.server_mute_manager import ServerMuteManager
    from src.nut.monitor import UpsMonitor

logger = logging.getLogger(__name__)

# Shown above the raw dump; everything else goes in the detailed listing.
_HEADLINE_VARS = (
    "battery.charge",
    "battery.runtime",
    "battery.voltage",
    "ups.load",
    "input.voltage",
    "output.voltage",
    "ups.temperature",
)


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def describe_status(raw: str | None) -> str:
    """Turn the ups.status token string into something readable.

    Keeps the raw tokens too: they are what every other NUT tool shows, and
    dropping them would make the bot harder to cross-check, not easier.
    """
    if not raw or not raw.strip():
        return "unknown"
    tokens = raw.split()
    known = [STATUS_MEANINGS[token] for token in tokens if token in STATUS_MEANINGS]
    if not known:
        return raw
    return f"{', '.join(known)} ({raw})"


def _status_icon(flags: set[str]) -> str:
    if flags & {"LB", "FSD", "ALARM", "OFF"}:
        return "\U0001f534"
    if flags & {"OB", "OVER", "BYPASS", "RB"}:
        return "⚠️"
    if "OL" in flags:
        return "✅"
    return "❓"


def format_ups(snapshot: dict[str, Any], detailed: bool = False) -> str:
    """Render a UPS snapshot for Telegram.

    An unreadable UPS renders as unavailable with the error attached. It never
    renders as a set of blanks, because blanks read as a healthy UPS.
    """
    target = snapshot.get("target", "the NUT server")

    if not snapshot.get("available"):
        error = snapshot.get("error") or "no reason given"
        return (
            "\U0001f50c *UPS*\n\n"
            f"⚠️ *Unavailable.* Cannot read `{target}`.\n"
            f"Last error: {error}\n\n"
            "_UPS state is unknown, which is not the same as healthy._"
        )

    variables: dict[str, str] = snapshot.get("variables") or {}
    flags = parse_status(variables.get("ups.status"))

    mfr = (variables.get("ups.mfr") or "").strip()
    model = (variables.get("ups.model") or "").strip()
    name = " ".join(part for part in (mfr, model) if part) or snapshot.get("ups") or "UPS"

    lines = [
        "\U0001f50c *UPS Status*\n",
        f"*{name}*",
        f"{_status_icon(flags)} {describe_status(variables.get('ups.status'))}",
        "",
    ]

    charge = _as_float(variables.get("battery.charge"))
    runtime = variables.get("battery.runtime")
    if charge is not None or runtime is not None:
        battery_bits = []
        if charge is not None:
            battery_bits.append(f"{charge:.0f}%")
        if runtime is not None:
            battery_bits.append(f"{format_runtime(runtime)} left")
        lines.append(f"*Battery:* {' • '.join(battery_bits)}")

    load = _as_float(variables.get("ups.load"))
    if load is not None:
        nominal = _as_float(variables.get("ups.realpower.nominal"))
        watts = f" (about {nominal * load / 100:.0f}W of {nominal:.0f}W)" if nominal else ""
        lines.append(f"*Load:* {load:.0f}%{watts}")

    input_v = _as_float(variables.get("input.voltage"))
    if input_v is not None:
        lines.append(f"*Input:* {input_v:.1f}V")

    on_battery_since = snapshot.get("on_battery_since")
    if "OB" in flags and on_battery_since is not None:
        lines.append(
            f"*On battery for:* {format_duration(time.monotonic() - on_battery_since)}"
        )

    if detailed:
        lines.append("\n*All variables:*")
        for key in sorted(variables):
            lines.append(f"`{key}` = {variables[key]}")
    else:
        extra = [k for k in _HEADLINE_VARS if k in variables and k not in (
            "battery.charge", "battery.runtime", "ups.load", "input.voltage",
        )]
        for key in extra:
            lines.append(f"*{key.split('.')[-1].title()}:* {variables[key]}")

    age = snapshot.get("age_seconds")
    if age is not None:
        lines.append(f"\n_Read {format_duration(age)} ago from {target}._")

    return "\n".join(lines)


def ups_command(
    ups_monitor: "UpsMonitor",
) -> Callable[[Message], Awaitable[None]]:
    """Factory for the /ups command handler."""

    async def handler(message: Message) -> None:
        text = (message.text or "").strip().lower()
        detailed = "detailed" in text or "full" in text

        if message.bot:
            await message.bot.send_chat_action(
                chat_id=message.chat.id, action=ChatAction.TYPING
            )

        snapshot = await ups_monitor.get_snapshot()
        await safe_reply(message, truncate_message(format_ups(snapshot, detailed=detailed)))

    return handler


def ups_mute_callback(
    mute_manager: "ServerMuteManager",
) -> Callable[[CallbackQuery], Awaitable[None]]:
    """Factory for the mute buttons on a UPS alert (ups_mute:<minutes>)."""

    async def handler(callback: CallbackQuery) -> None:
        raw = (callback.data or "").rsplit(":", 1)[-1]
        try:
            minutes = int(raw)
        except ValueError:
            await callback.answer("Bad mute duration")
            return
        if minutes <= 0:
            await callback.answer("Bad mute duration")
            return

        expiry = mute_manager.mute_ups(timedelta(minutes=minutes))
        await callback.answer("UPS alerts muted")
        if isinstance(callback.message, Message):
            await safe_reply(
                callback.message,
                f"\U0001f507 *Muted UPS alerts* {format_mute_expiry(expiry)}\n\n"
                f"Use `/unmute-server` to clear it early.",
            )

    return handler
