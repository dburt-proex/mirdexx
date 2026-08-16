from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated local-first runtime configuration."""

    data_dir: Path
    database_name: str = "mirdexx.db"
    api_host: str = "127.0.0.1"
    api_port: int = 8787

    def __post_init__(self) -> None:
        data_dir = Path(self.data_dir).expanduser().resolve(strict=False)
        object.__setattr__(self, "data_dir", data_dir)

        if not self.database_name or Path(self.database_name).name != self.database_name:
            raise ValueError("database_name must be a simple file name")
        if self.api_host not in _LOOPBACK_HOSTS:
            raise ValueError("Mirdexx API must remain bound to a loopback host")
        if not 1 <= self.api_port <= 65535:
            raise ValueError("api_port must be between 1 and 65535")

    @property
    def database_path(self) -> Path:
        return self.data_dir / self.database_name
