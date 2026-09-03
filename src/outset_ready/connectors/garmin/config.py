from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class GarminSettings:
    email: str | None
    password: str | None
    token_dir: Path
    data_dir: Path
    db_path: Path

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw" / "garmin"


def load_garmin_settings(env_file: str | Path | None = ".env") -> GarminSettings:
    if env_file is not None:
        load_dotenv(env_file, override=False)

    data_dir = Path(os.getenv("OUTSET_READY_DATA_DIR", "data")).expanduser()
    db_path = Path(
        os.getenv("OUTSET_READY_DB_PATH", str(data_dir / "outset_ready.sqlite"))
    ).expanduser()
    token_dir = Path(
        os.getenv(
            "GARMIN_TOKEN_DIR",
            os.getenv("GARMINTOKENS", "~/.garminconnect"),
        )
    ).expanduser()

    return GarminSettings(
        email=_optional_env("GARMIN_EMAIL"),
        password=_optional_env("GARMIN_PASSWORD"),
        token_dir=token_dir,
        data_dir=data_dir,
        db_path=db_path,
    )


def _optional_env(name: str) -> str | None:
    value = os.getenv(name)
    return value if value else None

