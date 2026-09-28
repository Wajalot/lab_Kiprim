"""Descubrimiento automático: qué /dev/ttyUSB* es cada instrumento, por *idn?."""
from __future__ import annotations

import glob
import logging

from .instrument import Instrument

log = logging.getLogger(__name__)


def parse_idn(idn: str) -> tuple[str, str, str]:
    """'KIPRIM,DC605S,24442462,FV:V5.4.0' -> (fabricante, modelo, serie)"""
    partes = idn.split(",")
    fabricante = partes[0] if len(partes) > 0 else ""
    modelo = partes[1] if len(partes) > 1 else ""
    serie = partes[2] if len(partes) > 2 else ""
    return fabricante, modelo, serie


def scan_ports(candidatos: list[str] | None = None) -> dict[str, dict]:
    """Devuelve {puerto: {"idn":..., "fabricante":..., "modelo":..., "serie":...}}"""
    if candidatos is None:
        candidatos = sorted(glob.glob("/dev/ttyUSB*")) + sorted(glob.glob("/dev/ttyACM*"))

    encontrados = {}
    for puerto in candidatos:
        try:
            inst = Instrument(puerto, timeout=0.5)
            idn = inst.idn()
            inst.close()
        except Exception as exc:
            log.debug("Puerto %s no respondió como instrumento SCPI: %s", puerto, exc)
            continue
        if not idn:
            continue
        fabricante, modelo, serie = parse_idn(idn)
        encontrados[puerto] = {"idn": idn, "fabricante": fabricante, "modelo": modelo, "serie": serie}
    return encontrados


def resolver_alias(encontrados: dict[str, dict], mapa_serie_alias: dict[str, str]) -> dict[str, str]:
    """{puerto: alias} para los instrumentos conocidos en la config."""
    resultado = {}
    for puerto, info in encontrados.items():
        alias = mapa_serie_alias.get(info["serie"])
        if alias:
            resultado[puerto] = alias
        else:
            log.warning("Instrumento en %s (serie %s, %s) no está en config.yaml -> instrumentos",
                        puerto, info["serie"], info["idn"])
    return resultado
