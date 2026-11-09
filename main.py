# coding: utf-8
"""Volume Agent - adjust the Booster K1 system output volume from the App UI."""

from pathlib import Path

from booster_agent_framework import (
    AgentBase,
    AgentFeatures,
    Component,
    DefaultStateIconComponent,
    IconComponent,
    LocaleString,
)
from boosteros.robots.booster import BoosterRobot

COMPONENT_VOLUME_STATUS = "volume_status"
COMPONENT_VOLUME_DOWN = "volume_down"
COMPONENT_VOLUME_UP = "volume_up"
COMPONENT_VOLUME_MUTE = "volume_mute"

# Volume is a float in [0.0, 1.0]; every click changes it by VOLUME_STEP.
VOLUME_STEP = 0.10
DEFAULT_VOLUME = 0.50
MUTE_EPSILON = 0.001

# Persisted inside the Agent storage directory (relative path is required).
STATE_FILE = Path("volume_state.txt")


def format_volume(volume: float) -> str:
    """Format a 0.0 - 1.0 volume value as a percentage string."""

    return f"{int(round(max(0.0, min(1.0, volume)) * 100))}%"


class VolumeAgent(AgentBase):
    """Agent that lets the user read and adjust the robot system volume."""

    def __init__(self) -> None:
        super().__init__(AgentFeatures(enable_auto_getup=True))
        self.robot = BoosterRobot()

        # Cached values keep the UI usable even if the audio subsystem
        # temporarily reports an error.
        self._cached_volume: float = DEFAULT_VOLUME
        self._last_nonzero_volume: float = DEFAULT_VOLUME

        self._load_state()
        self.setup_components()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    def on_agent_activated(self) -> None:
        """Called when the Agent is activated."""

        self.logger.info("VolumeAgent is activated")

        volume = self._read_volume()
        if volume is None:
            self.logger.warn(
                f"system volume unavailable on activation; "
                f"using cached volume {format_volume(self._cached_volume)}"
            )
            volume = self._cached_volume

        self._refresh_ui(volume)
        self.logger.info(f"current system volume: {format_volume(volume)}")

    def on_agent_close(self) -> None:
        """Called when the Agent is closing."""

        self._save_state()
        self.logger.info("VolumeAgent is closing")

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def setup_components(self) -> None:
        """Create and register the volume control buttons."""

        status_component = IconComponent(
            COMPONENT_VOLUME_STATUS,
            LocaleString(
                {"en": f"Volume {format_volume(self._cached_volume)}", "zh": f"音量 {format_volume(self._cached_volume)}"}
            ),
            "res/volume.png",
            self.on_volume_status_click,
            display_sequence=0,
        )
        self.component_manager.add_component(status_component)

        down_component = IconComponent(
            COMPONENT_VOLUME_DOWN,
            LocaleString({"en": "Volume -", "zh": "音量 -"}),
            "res/volume_down.png",
            self.on_volume_down_click,
            shortcut_id="volume_down",
            display_sequence=1,
        )
        self.component_manager.add_component(down_component)

        up_component = IconComponent(
            COMPONENT_VOLUME_UP,
            LocaleString({"en": "Volume +", "zh": "音量 +"}),
            "res/volume_up.png",
            self.on_volume_up_click,
            shortcut_id="volume_up",
            display_sequence=2,
        )
        self.component_manager.add_component(up_component)

        mute_component = DefaultStateIconComponent(
            COMPONENT_VOLUME_MUTE,
            LocaleString({"en": "Mute", "zh": "静音"}),
            "res/volume_mute.png",
            False,
            self.on_volume_mute_click,
            shortcut_id="volume_mute",
            display_sequence=3,
        )
        self.component_manager.add_component(mute_component)

    def on_volume_status_click(self, component: Component) -> LocaleString | None:
        """Show the current system volume and refresh the status button."""

        volume = self._read_volume()
        read_ok = volume is not None
        if volume is None:
            volume = self._cached_volume

        self._refresh_ui(volume)
        self.logger.info(
            f"volume status queried: component_id={component.id}, "
            + f"volume={format_volume(volume)}, read_ok={read_ok}"
        )

        if not read_ok:
            return LocaleString(
                {
                    "en": f"Volume read failed, cached {format_volume(volume)}",
                    "zh": f"读取音量失败，当前缓存值 {format_volume(volume)}",
                }
            )

        return LocaleString(
            {
                "en": f"Current volume {format_volume(volume)}",
                "zh": f"当前音量 {format_volume(volume)}",
            }
        )

    def on_volume_up_click(self, component: Component) -> LocaleString | None:
        """Increase the system volume by one step."""

        self.logger.info(f"volume up clicked: component_id={component.id}")
        return self._change_volume(VOLUME_STEP)

    def on_volume_down_click(self, component: Component) -> LocaleString | None:
        """Decrease the system volume by one step."""

        self.logger.info(f"volume down clicked: component_id={component.id}")
        return self._change_volume(-VOLUME_STEP)

    def on_volume_mute_click(self, component: Component) -> LocaleString | None:
        """Toggle between mute (0.0) and the last non-zero volume."""

        current = self._read_volume()
        if current is None:
            current = self._cached_volume

        if current > MUTE_EPSILON:
            self._last_nonzero_volume = current
            target = 0.0
            target_text = LocaleString({"en": "Muted", "zh": "已静音"})
        else:
            target = (
                self._last_nonzero_volume
                if self._last_nonzero_volume > MUTE_EPSILON
                else DEFAULT_VOLUME
            )
            target_text = LocaleString(
                {
                    "en": f"Unmuted, volume {format_volume(target)}",
                    "zh": f"已取消静音，音量 {format_volume(target)}",
                }
            )

        self.logger.info(
            f"mute toggled: component_id={component.id}, "
            + f"from={format_volume(current)}, to={format_volume(target)}"
        )

        applied = self._apply_volume(target)
        if applied is None:
            self._refresh_ui(self._cached_volume)
            return LocaleString(
                {"en": "Volume control unavailable", "zh": "音量调节不可用"}
            )

        self._refresh_ui(applied)
        return target_text

    # ------------------------------------------------------------------
    # Volume helpers
    # ------------------------------------------------------------------
    def _change_volume(self, delta: float) -> LocaleString | None:
        """Apply a relative volume change and return the resulting toast."""

        current = self._read_volume()
        if current is None:
            current = self._cached_volume

        target = round(max(0.0, min(1.0, current + delta)), 2)
        applied = self._apply_volume(target)

        if applied is None:
            self._refresh_ui(self._cached_volume)
            return LocaleString(
                {"en": "Volume control unavailable", "zh": "音量调节不可用"}
            )

        self._refresh_ui(applied)
        return LocaleString(
            {
                "en": f"Volume {format_volume(applied)}",
                "zh": f"音量 {format_volume(applied)}",
            }
        )

    def _read_volume(self) -> float | None:
        """Read the system volume; return None when the audio subsystem fails."""

        try:
            volume = float(self.robot.audio_manager.get_system_volume())
        except Exception as exc:
            self.logger.warn(
                f"get_system_volume failed: {exc.__class__.__name__}: {exc}"
            )
            return None

        volume = max(0.0, min(1.0, volume))
        self._cached_volume = volume
        if volume > MUTE_EPSILON:
            self._last_nonzero_volume = volume
        return volume

    def _apply_volume(self, volume: float) -> float | None:
        """Write the system volume; return the applied value or None on failure."""

        volume = round(max(0.0, min(1.0, float(volume))), 2)
        try:
            self.robot.audio_manager.set_system_volume(volume)
        except Exception as exc:
            self.logger.error(
                f"set_system_volume({volume}) failed: "
                + f"{exc.__class__.__name__}: {exc}"
            )
            return None

        self._cached_volume = volume
        if volume > MUTE_EPSILON:
            self._last_nonzero_volume = volume
        self._save_state()
        return volume

    def _refresh_ui(self, volume: float) -> None:
        """Sync the status label and the mute button state with the volume."""

        status = self.component_manager.get_component(COMPONENT_VOLUME_STATUS)
        if status is not None:
            status.name = LocaleString(
                {
                    "en": f"Volume {format_volume(volume)}",
                    "zh": f"音量 {format_volume(volume)}",
                }
            )
            self.component_manager.update_component(status)

        mute = self.component_manager.get_component(COMPONENT_VOLUME_MUTE)
        if mute is not None:
            try:
                mute.state = volume <= MUTE_EPSILON
                self.component_manager.update_component(mute)
            except Exception as exc:
                self.logger.warn(
                    f"mute button state update failed: {exc.__class__.__name__}: {exc}"
                )

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _load_state(self) -> None:
        """Restore the last non-zero volume so unmute can fall back to it."""

        try:
            if not self.storage_manager.file_exists(STATE_FILE):
                return
            text = self.storage_manager.read_text_file(STATE_FILE).strip()
            value = float(text)
            if MUTE_EPSILON < value <= 1.0:
                self._last_nonzero_volume = value
                self._cached_volume = value
        except Exception as exc:
            self.logger.warn(
                f"load volume state failed: {exc.__class__.__name__}: {exc}"
            )

    def _save_state(self) -> None:
        """Persist the last non-zero volume."""

        try:
            self.storage_manager.write_text_file(
                STATE_FILE, f"{self._last_nonzero_volume:.2f}"
            )
        except Exception as exc:
            self.logger.warn(
                f"save volume state failed: {exc.__class__.__name__}: {exc}"
            )