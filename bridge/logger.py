"""Log local en CSV por sesión, independiente de MQTT/InfluxDB/Grafana."""
from __future__ import annotations

import csv
import time
from pathlib import Path


class SessionLogger:
    def __init__(self, directorio: str | Path, alias: str):
        Path(directorio).mkdir(parents=True, exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.ruta = Path(directorio) / f"{alias}_{ts}.csv"
        self._f = open(self.ruta, "w", newline="", encoding="utf-8")
        self._w = csv.writer(self._f)
        self._w.writerow([
            "timestamp", "voltage_set", "current_limit", "output",
            "voltage_measured", "current_measured", "power_measured",
            "perfil", "paso",
        ])

    def registrar(self, estado: dict, perfil: str, paso) -> None:
        self._w.writerow([
            time.time(),
            estado["voltage_set"], estado["current_limit"], estado["output"],
            estado["voltage_measured"], estado["current_measured"],
            round(estado["voltage_measured"] * estado["current_measured"], 4),
            perfil, paso,
        ])
        self._f.flush()

    def cerrar(self) -> None:
        self._f.close()
