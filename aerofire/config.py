"""Loader for config/mission.yaml.

`load_config()` returns a `Config`: a read-only nested mapping with attribute
access (`cfg.tank.V_m3`). Overrides (e.g. a mission's fault injections) are
deep-merged on top of the file.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping

import yaml

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "mission.yaml"


class Config(Mapping[str, Any]):
    """Immutable nested config with attribute access."""

    def __init__(self, data: Mapping[str, Any]):
        self._data = {k: Config(v) if isinstance(v, Mapping) else v for k, v in data.items()}

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(f"config has no key '{name}'") from exc

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def to_dict(self) -> dict[str, Any]:
        return {k: v.to_dict() if isinstance(v, Config) else copy.deepcopy(v) for k, v in self._data.items()}

    def __repr__(self) -> str:
        return f"Config({self.to_dict()!r})"


def deep_merge(base: dict[str, Any], over: Mapping[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), dict):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def derive_site(data: dict[str, Any]) -> dict[str, Any]:
    """Everything site-dependent follows `site` (one place to change the launch site)."""
    site = data.get("site") or {}
    lat, lon = site.get("lat_deg"), site.get("lon_deg")
    if lat is None or lon is None:
        return data
    gates = data.get("gates") or {}
    if "approved_area_offsets_deg" in gates:
        gates["approved_area"] = [[round(lat + a, 6), round(lon + b, 6)] for a, b in gates["approved_area_offsets_deg"]]
    link = data.get("link")
    if isinstance(link, dict):
        link.setdefault("station_lat_deg", lat)
        link.setdefault("station_lon_deg", lon)
    return data


def load_raw(path: Path | str | None = None) -> dict[str, Any]:
    with open(path or DEFAULT_PATH, encoding="utf-8") as fh:
        return derive_site(yaml.safe_load(fh))


def load_config(path: Path | str | None = None, overrides: Mapping[str, Any] | None = None) -> Config:
    data = load_raw(path)
    if overrides:
        data = derive_site(deep_merge(data, overrides))
    return Config(data)
