"""Bridge principal: dueño único del puerto serie, habla con MQTT.

Uso: python -m bridge.service [config.yaml]
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

import paho.mqtt.client as mqtt

RAIZ = Path(__file__).resolve().parent.parent

from .config import cargar
from .discovery import scan_ports, resolver_alias
from .instrument import Instrument
from .logger import SessionLogger
from .profile import Profile

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("bridge")


def clamp(valor: float, minimo: float, maximo: float) -> float:
    return max(minimo, min(maximo, valor))


class Controlador:
    """Une un Instrument físico con su estado MQTT/perfil/log."""

    def __init__(self, alias: str, puerto: str, limites: dict, topic_prefix: str,
                 dir_sesiones: str, mqtt_client: mqtt.Client):
        self.alias = alias
        self.instrument = Instrument(puerto)
        self.limites = limites  # {"voltage_max":..., "current_max":...}
        self.topic_base = f"{topic_prefix}/{alias}"
        self.mqtt = mqtt_client
        self.logger = SessionLogger(dir_sesiones, alias)
        self.perfil: Profile | None = None
        self.lock = threading.RLock()  # el puerto serie no aguanta acceso concurrente
        log.info("[%s] conectado en %s (%s)", alias, puerto, self.instrument.idn())

    # --- topics ---
    def topic_cmd(self, sufijo: str) -> str:
        return f"{self.topic_base}/cmd/{sufijo}"

    def topic_state(self, sufijo: str) -> str:
        return f"{self.topic_base}/state/{sufijo}"

    # --- aplicar setpoints con límites absolutos ---
    def _aplicar_voltage_crudo(self, volts: float) -> None:
        v = clamp(volts, 0.0, self.limites["voltage_max"])
        self.instrument.set_voltage(v)

    def _aplicar_current_limit_crudo(self, amps: float) -> None:
        i = clamp(amps, 0.0, self.limites["current_max"])
        self.instrument.set_current_limit(i)

    def _aplicar_current_crudo(self, amps: float) -> None:
        i = clamp(amps, 0.0, self.limites["current_max"])
        self.instrument.set_current(i)

    def _aplicar_voltage_limit_crudo(self, volts: float) -> None:
        v = clamp(volts, 0.0, self.limites["voltage_max"])
        self.instrument.set_voltage_limit(v)

    def aplicar_output(self, on: bool) -> None:
        with self.lock:
            self.instrument.set_output(on)

    def cambiar_setpoints(self, voltage: float | None = None, current_limit: float | None = None,
                           current: float | None = None, voltage_limit: float | None = None) -> None:
        """Cambia voltage/current/current_limit/voltage_limit. Si la salida está encendida,
        la ciclamos (OFF -> fijar -> ON): mandar current:limit con la salida ya encendida y
        la corriente circulando cerca de ese valor disparaba el OCP (comprobado en banco,
        27-sep-2026) porque OCP y current:limit son el mismo registro en esta familia
        OWON/Kiprim. Aplicamos el mismo ciclo a voltage:limit por precaución, sin haber
        confirmado aún si comparte registro con OVP."""
        with self.lock:
            estaba_encendida = self.instrument.output_on()
            if estaba_encendida:
                self.instrument.set_output(False)
                time.sleep(0.3)
            if voltage is not None:
                self._aplicar_voltage_crudo(voltage)
            if current_limit is not None:
                self._aplicar_current_limit_crudo(current_limit)
            if current is not None:
                self._aplicar_current_crudo(current)
            if voltage_limit is not None:
                self._aplicar_voltage_limit_crudo(voltage_limit)
            if estaba_encendida:
                time.sleep(0.2)
                self.instrument.set_output(True)

    # --- comandos entrantes ---
    def manejar_comando(self, sufijo: str, payload: str) -> None:
        try:
            if sufijo == "voltage":
                self.cambiar_setpoints(voltage=float(payload))
            elif sufijo == "current_limit":
                self.cambiar_setpoints(current_limit=float(payload))
            elif sufijo == "current":
                self.cambiar_setpoints(current=float(payload))
            elif sufijo == "voltage_limit":
                self.cambiar_setpoints(voltage_limit=float(payload))
            elif sufijo == "output":
                self.aplicar_output(payload.strip().upper() in ("ON", "1", "TRUE"))
            elif sufijo == "profile/start":
                self.arrancar_perfil(payload.strip())
            elif sufijo == "profile/stop":
                self.parar_perfil()
            else:
                log.warning("[%s] comando desconocido: %s", self.alias, sufijo)
        except Exception as exc:
            log.exception("[%s] error aplicando comando %s=%s", self.alias, sufijo, payload)
            self.mqtt.publish(self.topic_state("error"), str(exc))

    def arrancar_perfil(self, nombre: str) -> None:
        ruta = RAIZ / "profiles" / f"{nombre}.yaml"
        self.perfil = Profile.cargar(ruta)
        paso = self.perfil.paso_actual
        self._aplicar_paso(paso)
        log.info("[%s] perfil '%s' arrancado", self.alias, nombre)

    def parar_perfil(self) -> None:
        if self.perfil:
            log.info("[%s] perfil '%s' detenido manualmente", self.alias, self.perfil.nombre)
        self.perfil = None

    def _aplicar_paso(self, paso: dict) -> None:
        voltage = paso.get("voltage")
        current = paso.get("current")
        current_limit = paso.get("current_limit")
        voltage_limit = paso.get("voltage_limit")
        if any(x is not None for x in (voltage, current, current_limit, voltage_limit)):
            self.cambiar_setpoints(voltage=voltage, current=current,
                                    current_limit=current_limit, voltage_limit=voltage_limit)
        if "output" in paso:
            self.aplicar_output(bool(paso["output"]) if not isinstance(paso["output"], str)
                                 else paso["output"].strip().lower() != "off")

    # --- tick periódico ---
    def tick(self) -> None:
        with self.lock:
            estado = self.instrument.read_all()

            if self.perfil and not self.perfil.terminado:
                if self.perfil.condicion_cumplida(estado["voltage_measured"], estado["current_measured"]):
                    self.perfil.avanzar()
                    siguiente = self.perfil.paso_actual
                    if siguiente is not None:
                        self._aplicar_paso(siguiente)
                        log.info("[%s] perfil '%s' -> paso %d", self.alias, self.perfil.nombre, self.perfil.indice)
                        estado = self.instrument.read_all()
                    else:
                        log.info("[%s] perfil '%s' terminado", self.alias, self.perfil.nombre)
                        self.perfil = None

        # publicar estado
        self.mqtt.publish(self.topic_state("output"), "ON" if estado["output"] else "OFF", retain=True)
        self.mqtt.publish(self.topic_state("voltage_set"), estado["voltage_set"], retain=True)
        self.mqtt.publish(self.topic_state("current_set"), estado["current_set"], retain=True)
        self.mqtt.publish(self.topic_state("current_limit"), estado["current_limit"], retain=True)
        self.mqtt.publish(self.topic_state("voltage_limit"), estado["voltage_limit"], retain=True)
        self.mqtt.publish(self.topic_state("voltage_measured"), estado["voltage_measured"])
        self.mqtt.publish(self.topic_state("current_measured"), estado["current_measured"])
        self.mqtt.publish(self.topic_state("power_measured"),
                           round(estado["voltage_measured"] * estado["current_measured"], 4))
        nombre_perfil = self.perfil.nombre if self.perfil else "ninguno"
        paso_actual = self.perfil.indice if self.perfil else -1
        self.mqtt.publish(self.topic_state("profile"), nombre_perfil, retain=True)
        self.mqtt.publish(self.topic_state("profile_step"), paso_actual, retain=True)

        cond = self.perfil.condicion_actual if self.perfil else None
        self.mqtt.publish(self.topic_state("profile_condicion"), cond[0] if cond else "")
        self.mqtt.publish(self.topic_state("profile_condicion_valor"), cond[1] if cond else "")
        restante = self.perfil.tiempo_restante if self.perfil else None
        self.mqtt.publish(self.topic_state("profile_tiempo_restante"),
                           round(restante, 1) if restante is not None else "")

        self.logger.registrar(estado, nombre_perfil, paso_actual)


def main(ruta_config: str = "config.yaml") -> None:
    cfg = cargar(ruta_config)
    prefix = cfg["mqtt"]["topic_prefix"]

    encontrados = scan_ports()
    alias_por_puerto = resolver_alias(encontrados, cfg["instrumentos"])
    if not alias_por_puerto:
        log.error("Ningún instrumento conocido detectado. Encontrados: %s", encontrados)
        sys.exit(1)

    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
    client.connect(cfg["mqtt"]["host"], cfg["mqtt"]["port"])

    controladores: dict[str, Controlador] = {}
    for puerto, alias in alias_por_puerto.items():
        controladores[alias] = Controlador(
            alias=alias, puerto=puerto, limites=cfg["limites"][alias],
            topic_prefix=prefix, dir_sesiones=cfg["logging"]["dir"], mqtt_client=client,
        )

    def on_message(client, userdata, msg):
        # lab/<alias>/cmd/<sufijo...>
        partes = msg.topic.split("/")
        if len(partes) < 4 or partes[0] != prefix or partes[2] != "cmd":
            return
        alias = partes[1]
        sufijo = "/".join(partes[3:])
        ctrl = controladores.get(alias)
        if ctrl:
            ctrl.manejar_comando(sufijo, msg.payload.decode())

    client.on_message = on_message
    client.subscribe(f"{prefix}/+/cmd/#")
    client.loop_start()

    intervalo = cfg["logging"]["intervalo_s"]
    log.info("Bridge en marcha. Instrumentos: %s", list(controladores.keys()))
    try:
        while True:
            for ctrl in controladores.values():
                try:
                    ctrl.tick()
                except Exception as exc:
                    log.warning("[%s] tick fallido (se reintenta el siguiente ciclo): %s", ctrl.alias, exc)
                    ctrl.instrument.ser.reset_input_buffer()
            time.sleep(intervalo)
    except KeyboardInterrupt:
        pass
    finally:
        for ctrl in controladores.values():
            ctrl.logger.cerrar()
            ctrl.instrument.close()
        client.loop_stop()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "config.yaml")
