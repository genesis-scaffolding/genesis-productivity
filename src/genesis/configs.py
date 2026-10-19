"""Configuration management for the project.

Loads from YAML file, then applies environment variable overrides.
User is extracted from the config file path structure.

Priority (lowest to highest):
  1. YAML file
  2. Environment variables (e.g., GENESIS__DAV__PASSWORD)
"""

from __future__ import annotations

import os
import re
import socket
from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings

# -----------------------------------------------------------------------------
# Module-level defaults
# -----------------------------------------------------------------------------

DEFAULT_APP_NAME = "genesis"
DEFAULT_USER = "default"

# Env var used by the CLI to hand the resolved config path to the UI subprocess.
# Both sides import this constant so they cannot drift.
CONFIG_PATH_ENV = "GENESIS__CONFIG_PATH"

DEFAULT_DAV_HOST = "127.0.0.1"
DEFAULT_DAV_PORT = 5232
DEFAULT_DAV_USERNAME = "genesis"
DEFAULT_DAV_PASSWORD = "genesis-local"

DEFAULT_CONFIG_HOME = Path.home() / ".config"
DEFAULT_DATA_HOME = Path.home() / ".local" / "share"
DEFAULT_STATE_HOME = Path.home() / ".local" / "state"
DEFAULT_CACHE_HOME = Path.home() / ".cache"

# ntfy defaults. ADR-027.
DEFAULT_NTFY_URL = "http://p3:8092"
DEFAULT_NTFY_TOPIC_HOST = "localhost"


# -----------------------------------------------------------------------------
# Pydantic models
# -----------------------------------------------------------------------------


class Paths(BaseModel):
    """XDG-based paths for the application.

    Most paths are resolved relative to ``<xdg_base>/<app_name>/<user>/``
    so that per-user config, cache, and state stay isolated on disk.

    Three fields are SHARED across users: ``radicale_root``,
    ``journal_root``, and ``api_secret_key``. They live under
    ``<xdg_data>/<app_name>/`` so one Radicale instance and one
    JWT signing key serve every account (ADR-019).

    ``profile_root`` is per-user (ADR-025): each account gets its
    own profile and life-story files. Resolves under
    ``<xdg_data>/<app_name>/<user>/profile``.
    """

    config: Path = Field(description="Directory for config files (per-user).")
    data: Path = Field(description="Directory for persistent data (per-user).")
    state: Path = Field(description="Directory for logs and runtime state (per-user).")
    cache: Path = Field(description="Directory for cache files (per-user).")
    radicale_root: Path = Field(
        description="Shared Radicale data root. One Radicale serves all users.",
    )
    journal_root: Path = Field(
        description="Shared root under which each user gets a per-user journal subdir.",
    )
    api_secret_key: Path = Field(
        description="Shared JWT signing key. Rotating it invalidates every user's tokens.",
    )
    profile_root: Path = Field(
        description="Per-user profile directory. Resolves under data/, namespaced by user.",
    )


class DAV(BaseModel):
    """CalDAV and CardDAV connection settings."""

    host: str = Field(default=DEFAULT_DAV_HOST, description="DAV server host.")
    port: int = Field(default=DEFAULT_DAV_PORT, description="DAV server port.")
    username: str = Field(default="", description="Username for DAV authentication.")
    password: str = Field(default="", description="Password for DAV authentication.")

    @property
    def server_url(self) -> str:
        """Construct server URL from host and port."""
        return f"http://{self.host}:{self.port}"


class StatConfig(BaseModel):
    """One stat tracked by the game layer (ADR-026).

    Attributes:
        id: Stable identifier referenced by XP events and game
            facade calls. Snake_case ASCII; renaming an id is a
            breaking change to stored events.
        name: Display name shown in the UI. Renaming is safe; it
            does not affect event storage.
    """

    id: str
    name: str


# Default stat set from the Real-Life-Persona-System design note.
# Editable per-user via configs.yaml; this is just the starter.
DEFAULT_GAME_STATS: list[StatConfig] = [
    StatConfig(id="courage", name="Courage"),
    StatConfig(id="discipline", name="Discipline"),
    StatConfig(id="charisma", name="Charisma"),
    StatConfig(id="knowledge", name="Knowledge"),
    StatConfig(id="vitality", name="Vitality"),
]


class GameConfig(BaseModel):
    """Life-gamification layer settings (ADR-026).

    ``enabled`` gates every aspect of the layer: when False, the
    game facade raises ``GameDisabled`` and no XP events are
    written. ``stats`` is the set of stats the user tracks; per-user
    defaults from ``DEFAULT_GAME_STATS``.

    Level labels, the XP curve, and per-event XP magnitudes are
    intentionally NOT configurable per user — they are hardcoded
    constants in ``genesis.game``. Changing them is a code release.
    """

    enabled: bool = True
    stats: list[StatConfig] = Field(default_factory=lambda: list(DEFAULT_GAME_STATS))


class NtfyConfig(BaseModel):
    """ntfy notification server settings. ADR-027.

    ``url`` is the ntfy server base URL. ``topic`` is the channel
    name the focus page POSTs to. Both are per-user because
    ``configs.yaml`` is per-user; a user can override the URL
    when running ntfy on a different host, and can override the
    topic to disambiguate two machines sharing one ntfy server.

    Topic defaults are resolved at Facade construction time (see
    :meth:`resolved_topic` and ``_build_ntfy`` below): when the
    field is empty, the runtime default is
    ``genesis-<safe-host>-<user>``. The field stays empty in
    YAML/env to signal "use the default".
    """

    url: str = Field(default=DEFAULT_NTFY_URL, description="ntfy server base URL.")
    topic: str = Field(default="", description="ntfy topic name. Empty means use the runtime default.")

    def resolved_topic(self, user: str) -> str:
        """Return the topic to POST to, resolving the default when empty.

        The default is ``genesis-<safe_host>-<user>``. ``user``
        is the per-user namespace, supplied by the caller
        (``Configs.user``); the safe host comes from
        :func:`_safe_hostname`.
        """
        if self.topic:
            return self.topic
        return f"genesis-{_safe_hostname()}-{user}"


def _safe_hostname() -> str:
    """Return a normalised hostname suitable for the ntfy topic.

    Lowercases the first label of ``socket.gethostname()`` and
    restricts it to ``[a-z0-9-]``. Falls back to
    :data:`DEFAULT_NTFY_TOPIC_HOST` on any failure (empty
    hostname, ``socket.error``, no alphanumerics left after
    sanitisation).

    Used by :meth:`NtfyConfig.resolved_topic` so two machines
    sharing one ntfy server get different topic names by default
    (FQDN suffixes and docker / kubernetes suffixes are dropped).
    """
    try:
        raw = socket.gethostname() or ""
    except OSError:
        return DEFAULT_NTFY_TOPIC_HOST
    first = raw.split(".", 1)[0].lower()
    cleaned = re.sub(r"[^a-z0-9-]", "-", first).strip("-")
    return cleaned or DEFAULT_NTFY_TOPIC_HOST


class Configs(BaseSettings):
    """Root configuration object.

    Loads from YAML file (if exists), then applies environment variable overrides.
    User is derived from the config file path.

    Environment variables use double-underscore separator:
      e.g. GENESIS__DAV__SERVER_URL, GENESIS__PATHS__DATA
    """

    app_name: str = Field(default=DEFAULT_APP_NAME)
    user: str = Field(default=DEFAULT_USER)
    paths: Paths = Field(description="XDG-based directory paths.")
    dav: DAV = Field(description="CalDAV and CardDAV settings.")
    game: GameConfig = Field(default_factory=GameConfig)
    ntfy: NtfyConfig = Field(default_factory=NtfyConfig)

    model_config = {
        "extra": "ignore",
    }

    def __init__(
        self,
        yaml_path: str | Path | None = None,
        **kwargs,
    ) -> None:
        # Determine yaml_path
        if yaml_path is None:
            config_home = os.environ.get("XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME))
            yaml_path = Path(config_home) / DEFAULT_APP_NAME / DEFAULT_USER / "configs.yaml"
        else:
            yaml_path = Path(yaml_path).resolve()

        # Extract user from path
        user = _extract_user(yaml_path)

        # Load YAML if exists
        yaml_data: dict = {}
        if yaml_path.exists():
            with open(yaml_path) as f:
                yaml_data = yaml.safe_load(f) or {}

        # Build paths scoped to genesis/<user>/
        yaml_data["paths"] = _build_paths(yaml_data, user)

        # Build dav config
        yaml_data["dav"] = _build_dav(yaml_data, user)

        # Build ntfy config
        yaml_data["ntfy"] = _build_ntfy(yaml_data)

        # Merge: yaml_data < env_vars < explicit kwargs
        merged = {**yaml_data, **kwargs}
        merged["user"] = user

        super().__init__(**merged)


# -----------------------------------------------------------------------------
# Helper functions
# -----------------------------------------------------------------------------


def _extract_user(yaml_path: Path | None) -> str:
    """Extract user from yaml_path directory structure.

    Expected: <xdg_config_home>/genesis/<user>/configs.yaml
    Falls back to DEFAULT_USER if path doesn't match expected pattern.

    The check for `user != "configs.yaml"` keeps backward compatibility with
    the legacy flat path (~/.config/genesis/configs.yaml). With the implicit
    default path now scoped under genesis/default/, this branch only fires
    for legacy paths passed explicitly.
    """
    if yaml_path is None:
        return DEFAULT_USER

    parts = yaml_path.parts
    try:
        # Match the LAST occurrence: the app dir is the innermost one. A
        # first-occurrence match breaks when an ancestor directory is also
        # named after the app, e.g. the Docker image user "genesis" whose
        # home is /home/genesis -> /home/genesis/.config/genesis/<user>/
        # configs.yaml would extract ".config" as the user.
        genesis_idx = len(parts) - 1 - parts[::-1].index(DEFAULT_APP_NAME)
        if len(parts) >= genesis_idx + 2:
            user = parts[genesis_idx + 1]
            if user != "configs.yaml":
                return user
    except ValueError:
        pass

    return DEFAULT_USER


def resolve_config_path(user: str | None = None) -> Path:
    """Resolve a user name to a config file path.

    Always returns an explicit path under
    <xdg_config_home>/genesis/<user>/configs.yaml.

    For the default user, creates the parent directory and seeds an empty
    configs.yaml at the resolved path if it does not already exist. For
    other users, only the parent directory is created; the configs.yaml
    is left for the user to provide (defaults apply if absent).

    Args:
        user: User name. None or empty string resolves to DEFAULT_USER.

    Returns:
        Absolute path to the configs.yaml file. The parent directory
        always exists after this call.
    """
    if user is None or user == "":
        user = DEFAULT_USER

    config_home = os.environ.get("XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME))
    config_path = Path(config_home) / DEFAULT_APP_NAME / user / "configs.yaml"

    config_path.parent.mkdir(parents=True, exist_ok=True)

    if user == DEFAULT_USER and not config_path.exists():
        config_path.write_text("")

    return config_path


def _build_paths(raw: dict, user: str) -> dict:
    """Build the paths dict.

    Per-user fields resolve under ``<xdg_base>/<app_name>/<user>/``.
    Shared fields (``radicale_root``, ``journal_root``,
    ``api_secret_key``) resolve under ``<xdg_data>/<app_name>/``
    so that one Radicale instance and one JWT signing key serve
    every account. YAML may override any field.
    """
    config_home = os.environ.get("XDG_CONFIG_HOME", str(DEFAULT_CONFIG_HOME))
    data_home = os.environ.get("XDG_DATA_HOME", str(DEFAULT_DATA_HOME))
    state_home = os.environ.get("XDG_STATE_HOME", str(DEFAULT_STATE_HOME))
    cache_home = os.environ.get("XDG_CACHE_HOME", str(DEFAULT_CACHE_HOME))

    user_prefix = f"{DEFAULT_APP_NAME}/{user}"
    shared_prefix = f"{data_home}/{DEFAULT_APP_NAME}"

    yaml_paths = raw.get("paths", {})
    config_base = yaml_paths.get("config") or f"{config_home}/{user_prefix}"
    data_base = yaml_paths.get("data") or f"{data_home}/{user_prefix}"
    state_base = yaml_paths.get("state") or f"{state_home}/{user_prefix}"
    cache_base = yaml_paths.get("cache") or f"{cache_home}/{user_prefix}"
    radicale_root = yaml_paths.get("radicale_root") or f"{shared_prefix}/radicale"
    journal_root = yaml_paths.get("journal_root") or f"{shared_prefix}/journals"
    api_secret_key = yaml_paths.get("api_secret_key") or f"{shared_prefix}/api_secret.key"
    profile_root = yaml_paths.get("profile_root") or f"{data_home}/{user_prefix}/profile"

    return {
        "config": config_base,
        "data": data_base,
        "state": state_base,
        "cache": cache_base,
        "radicale_root": radicale_root,
        "journal_root": journal_root,
        "api_secret_key": api_secret_key,
        "profile_root": profile_root,
    }


def _build_dav(raw: dict, user: str) -> dict:
    """Build the DAV config dict from YAML or defaults."""
    yaml_dav = raw.get("dav", {})
    return {
        "host": os.environ.get("GENESIS__DAV__HOST") or yaml_dav.get("host", DEFAULT_DAV_HOST),
        "port": int(os.environ.get("GENESIS__DAV__PORT") or yaml_dav.get("port", DEFAULT_DAV_PORT)),
        "username": os.environ.get("GENESIS__DAV__USERNAME")
        or yaml_dav.get("username", user),  # Default to extracted user
        "password": os.environ.get("GENESIS__DAV__PASSWORD") or yaml_dav.get("password", DEFAULT_DAV_PASSWORD),
    }


def _build_ntfy(raw: dict) -> dict:
    """Build the ntfy config dict from YAML or defaults. ADR-027.

    ``url`` resolves env var → YAML → ``DEFAULT_NTFY_URL`` and
    is stripped of any trailing slash so the focus page builds
    ``{url}/{topic}`` without double slashes. ``topic`` resolves
    env var → YAML → empty string; an empty topic means
    "use the runtime default" and is resolved by
    :meth:`NtfyConfig.resolved_topic` at Facade construction
    (when the username is known).
    """
    yaml_ntfy = raw.get("ntfy", {})
    url = os.environ.get("GENESIS__NTFY__URL") or yaml_ntfy.get("url") or DEFAULT_NTFY_URL
    url = url.rstrip("/") or DEFAULT_NTFY_URL
    topic = os.environ.get("GENESIS__NTFY__TOPIC") or yaml_ntfy.get("topic") or ""
    return {"url": url, "topic": topic}
