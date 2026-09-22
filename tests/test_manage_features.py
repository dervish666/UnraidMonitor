"""Features panel toggles in /manage."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "factory_name, config_attr, restarting, no_restart",
    [
        ("feat_image_toggle_callback", "img", "♻️ Enabling image updates — restarting to apply…",
         "Image updates enabled. Restart the bot to apply."),
        ("feat_ups_toggle_callback", "ups", "♻️ Enabling UPS monitoring — restarting to apply…",
         "UPS monitoring enabled. Restart the bot to apply."),
        ("feat_notifications_callback", "notif", "♻️ Enabling Unraid notifications — restarting to apply…",
         "Unraid notifications enabled. Restart the bot to apply."),
    ],
)
async def test_restart_toggle_wording(factory_name, config_attr, restarting, no_restart):
    """The three restart-to-apply toggles share one helper; pin their text."""
    from unittest.mock import AsyncMock, MagicMock, patch
    from src.bot import manage_command

    factory = getattr(manage_command, factory_name)
    for restart_cb, expected in ((AsyncMock(), restarting), (None, no_restart)):
        callback = MagicMock()
        callback.data = f"feat:{config_attr}:on"
        callback.answer = AsyncMock()
        with patch.object(manage_command, "safe_edit", new=AsyncMock()) as edit:
            await factory(MagicMock(), restart_cb=restart_cb)(callback)
        assert edit.call_args[0][1] == expected
