"""Lab settings, read from the environment.

`make` loads `.env` into the environment with `uv run --env-file .env`; `.env.example` lists
every variable. Only the credentials are required; everything else has a lab default.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

REQUIRED = ("FLOE_LAB_S3_ACCESS_KEY", "FLOE_LAB_S3_SECRET_KEY")


class SettingsError(RuntimeError):
    """A required setting is missing."""


@dataclass(frozen=True)
class Settings:
    s3_access_key: str
    s3_secret_key: str
    s3_region: str = "local-01"
    # The S3 API as the host sees it, and as containers on the Compose network see it.
    s3_endpoint: str = "http://127.0.0.1:9000"
    s3_internal_endpoint: str = "http://rustfs:9000"
    bucket: str = "floe-lab"
    key_prefix: str = "warehouse/"
    catalog_url: str = "http://127.0.0.1:8181"
    warehouse: str = "lab"
    namespace: str = "sales"

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        env = os.environ if env is None else env
        missing = [name for name in REQUIRED if not env.get(name)]
        if missing:
            raise SettingsError(
                f"missing {', '.join(missing)} in the environment. "
                "Run through make (it loads .env, created from .env.example by `make up`)."
            )
        return cls(
            s3_access_key=env["FLOE_LAB_S3_ACCESS_KEY"],
            s3_secret_key=env["FLOE_LAB_S3_SECRET_KEY"],
            s3_region=env.get("FLOE_LAB_S3_REGION", cls.s3_region),
        )

    @property
    def management_url(self) -> str:
        return f"{self.catalog_url}/management/v1"

    @property
    def rest_catalog_url(self) -> str:
        return f"{self.catalog_url}/catalog"
