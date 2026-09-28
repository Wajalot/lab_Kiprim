"""Motor de perfiles: máquina de estados sobre pasos definidos en YAML."""
from __future__ import annotations

import time
from pathlib import Path

import yaml


class Profile:
    def __init__(self, nombre: str, pasos: list[dict]):
        self.nombre = nombre
        self.pasos = pasos
        self.indice = 0
        self.inicio_paso = time.time()

    @classmethod
    def cargar(cls, ruta: str | Path) -> "Profile":
        with open(ruta, encoding="utf-8") as f:
            datos = yaml.safe_load(f)
        return cls(datos["nombre"], datos["pasos"])

    @property
    def paso_actual(self) -> dict | None:
        if self.indice >= len(self.pasos):
            return None
        return self.pasos[self.indice]

    @property
    def terminado(self) -> bool:
        return self.indice >= len(self.pasos)

    def condicion_cumplida(self, medida_v: float, medida_i: float) -> bool:
        paso = self.paso_actual
        if paso is None:
            return True
        hasta = paso.get("hasta")
        if not hasta:
            return True  # paso sin condición: se aplica y el perfil termina
        if "tiempo" in hasta:
            return (time.time() - self.inicio_paso) >= hasta["tiempo"]
        if "corriente_medida_bajo" in hasta:
            return medida_i < hasta["corriente_medida_bajo"]
        if "corriente_medida_alto" in hasta:
            return medida_i > hasta["corriente_medida_alto"]
        if "voltage_medido_bajo" in hasta:
            return medida_v < hasta["voltage_medido_bajo"]
        if "voltage_medido_alto" in hasta:
            return medida_v > hasta["voltage_medido_alto"]
        return False

    def avanzar(self) -> None:
        self.indice += 1
        self.inicio_paso = time.time()

    @property
    def condicion_actual(self) -> tuple[str, float] | None:
        """(clave, valor) de la condición 'hasta' del paso actual, o None si no tiene."""
        paso = self.paso_actual
        if paso is None:
            return None
        hasta = paso.get("hasta")
        if not hasta:
            return None
        clave, valor = next(iter(hasta.items()))
        return clave, valor

    @property
    def tiempo_restante(self) -> float | None:
        """Segundos que quedan si la condición actual es de tiempo; None si no aplica."""
        cond = self.condicion_actual
        if cond is None or cond[0] != "tiempo":
            return None
        return max(0.0, cond[1] - (time.time() - self.inicio_paso))
