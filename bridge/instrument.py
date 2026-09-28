"""Driver serie para fuentes Kiprim/OWON (protocolo SCPI-like, 115200 8N1)."""
from __future__ import annotations

import time
import serial


class ProtocolError(Exception):
    pass


class Instrument:
    def __init__(self, port: str, timeout: float = 1.0):
        self.port = port
        self.ser = serial.Serial(port, baudrate=115200, bytesize=8, parity="N",
                                  stopbits=1, timeout=timeout)
        time.sleep(0.2)

    def close(self):
        self.ser.close()

    def _query(self, cmd: str) -> str:
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode())
        raw = self.ser.readline()
        resp = raw.decode(errors="replace").strip()
        if resp == "ERR":
            raise ProtocolError(f"{cmd!r} -> ERR")
        return resp

    def _write(self, cmd: str) -> None:
        self.ser.write((cmd + "\n").encode())

    # --- identificación ---
    def idn(self) -> str:
        return self._query("*idn?")

    # --- lectura ---
    def output_on(self) -> bool:
        return self._query("output?") == "ON"

    def voltage_set(self) -> float:
        return float(self._query("voltage?"))

    def current_set(self) -> float:
        return float(self._query("current?"))

    def voltage_limit(self) -> float:
        return float(self._query("voltage:limit?"))

    def current_limit(self) -> float:
        return float(self._query("current:limit?"))

    def measure_voltage(self) -> float:
        return float(self._query("measure:voltage?"))

    def measure_current(self) -> float:
        return float(self._query("measure:current?"))

    def read_all(self) -> dict:
        return {
            "output": self.output_on(),
            "voltage_set": self.voltage_set(),
            "current_set": self.current_set(),
            "current_limit": self.current_limit(),
            "voltage_limit": self.voltage_limit(),
            "voltage_measured": self.measure_voltage(),
            "current_measured": self.measure_current(),
        }

    # --- escritura ---
    def set_voltage(self, volts: float) -> None:
        self._write(f"voltage {volts:.3f}")

    def set_current(self, amps: float) -> None:
        self._write(f"current {amps:.3f}")

    def set_voltage_limit(self, volts: float) -> None:
        self._write(f"voltage:limit {volts:.3f}")

    def set_current_limit(self, amps: float) -> None:
        self._write(f"current:limit {amps:.3f}")

    def set_output(self, on: bool) -> None:
        self._write(f"output {1 if on else 0}")
