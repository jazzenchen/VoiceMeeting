from __future__ import annotations

import sys
import types
from importlib import metadata
from importlib import resources
from pathlib import Path
from typing import Any


def ensure_pkg_resources_compat() -> None:
    try:
        import pkg_resources  # noqa: F401

        return
    except ModuleNotFoundError:
        pass

    class DistributionNotFound(Exception):
        pass

    class Distribution:
        def __init__(self, dist: metadata.Distribution) -> None:
            self._dist = dist
            self.project_name = dist.metadata.get("Name") or ""
            self.version = dist.version

        def get_metadata(self, name: str) -> str:
            value = self._dist.read_text(name)
            if value is None:
                raise FileNotFoundError(name)
            return value

    def get_distribution(name: str) -> Distribution:
        try:
            return Distribution(metadata.distribution(name))
        except metadata.PackageNotFoundError as exc:
            raise DistributionNotFound(name) from exc

    def resource_filename(package_or_requirement: Any, resource_name: str) -> str:
        package = str(package_or_requirement).split()[0]
        return str(Path(resources.files(package)).joinpath(resource_name))

    module = types.ModuleType("pkg_resources")
    module.DistributionNotFound = DistributionNotFound
    module.get_distribution = get_distribution
    module.resource_filename = resource_filename
    sys.modules["pkg_resources"] = module
