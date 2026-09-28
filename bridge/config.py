from __future__ import annotations

from pathlib import Path
import yaml


def cargar(ruta: str | Path = "config.yaml") -> dict:
    with open(ruta, encoding="utf-8") as f:
        return yaml.safe_load(f)
