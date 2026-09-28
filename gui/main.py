"""GUI de control — cliente MQTT desacoplado del bridge (PySide6 + pyqtgraph)."""
from __future__ import annotations

import sys
import time
from pathlib import Path

import paho.mqtt.client as mqtt
import pyqtgraph as pg
import yaml
from PySide6.QtCore import QLocale, QObject, QTimer, Signal, Qt
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDoubleSpinBox, QFrame, QGridLayout, QGroupBox, QHBoxLayout,
    QLabel, QMainWindow, QPushButton, QVBoxLayout, QWidget,
)

ESTILO = """
QWidget { background-color: #14181f; color: #d8dee9; font-family: 'Segoe UI', sans-serif; }
QGroupBox {
    border: 1px solid #2a3040; border-radius: 8px; margin-top: 10px;
    font-weight: 600; color: #8892a6; padding-top: 6px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; }
QPushButton {
    background-color: #2a3040; border: 1px solid #3a4358; border-radius: 6px;
    padding: 6px 14px; color: #d8dee9;
}
QPushButton:hover { background-color: #3a4358; }
QPushButton:checked { background-color: #2f6f4f; border-color: #3f9f6f; }
QDoubleSpinBox, QComboBox {
    background-color: #1c212b; border: 1px solid #2a3040; border-radius: 4px; padding: 3px;
}
"""


class TarjetaEstado(QFrame):
    """Tarjeta grande estilo dashboard para una lectura en tiempo real."""

    def __init__(self, titulo: str, unidad: str, color: str):
        super().__init__()
        self.setFrameShape(QFrame.StyledPanel)
        self.setStyleSheet(f"""
            TarjetaEstado {{
                background-color: #1a1f29; border: 1px solid #2a3040;
                border-radius: 10px;
            }}
        """)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 12)
        lbl_titulo = QLabel(titulo.upper())
        lbl_titulo.setStyleSheet("color: #6b7488; font-size: 10pt; font-weight: 600; border: none;")
        self.lbl_valor = QLabel("—")
        self.lbl_valor.setStyleSheet(f"color: {color}; font-size: 28pt; font-weight: 700; border: none;")
        lbl_unidad = QLabel(unidad)
        lbl_unidad.setStyleSheet("color: #6b7488; font-size: 9pt; border: none;")
        layout.addWidget(lbl_titulo)
        layout.addWidget(self.lbl_valor)
        layout.addWidget(lbl_unidad)

    def actualizar(self, valor: str) -> None:
        self.lbl_valor.setText(valor)


class Insignia(QLabel):
    """Etiqueta tipo 'pill' para estados on/off."""

    def __init__(self, texto: str = "—"):
        super().__init__(texto)
        self.setAlignment(Qt.AlignCenter)
        self._pintar(False)

    def _pintar(self, activo: bool, color_inactivo: str = "#3a4358") -> None:
        color = "#2f6f4f" if activo else color_inactivo
        self.setStyleSheet(
            f"background-color: {color}; color: white; border-radius: 10px; "
            f"padding: 4px 12px; font-weight: 600;"
        )

    def set_estado(self, texto: str, activo: bool, color_inactivo: str = "#3a4358") -> None:
        self.setText(texto)
        self._pintar(activo, color_inactivo)


class SpinBoxSeleccionable(QDoubleSpinBox):
    """QDoubleSpinBox con separador decimal fijo (punto) y selección total del
    valor al entrar, para poder pisar el número escribiendo directamente sin
    tener que borrarlo antes a mano."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setLocale(QLocale(QLocale.Language.English, QLocale.Country.UnitedStates))

    def focusInEvent(self, event):
        super().focusInEvent(event)
        self.selectAll()


class BarraTitulo(QWidget):
    """Barra de título propia para la ventana sin marco: arrastre + minimizar/maximizar/cerrar."""

    def __init__(self, ventana: QMainWindow, titulo: str):
        super().__init__()
        self.ventana = ventana
        self.setFixedHeight(36)
        self.setStyleSheet("background-color: #1a1f29; border-bottom: 1px solid #2a3040;")

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 6, 0)
        lbl_icono = QLabel()
        lbl_icono.setPixmap(QIcon.fromTheme("kiprim-gui").pixmap(22, 22))
        lbl_icono.setStyleSheet("border: none;")
        layout.addWidget(lbl_icono)
        layout.addSpacing(8)
        lbl = QLabel(titulo)
        lbl.setStyleSheet("color: #d8dee9; font-weight: 600; border: none;")
        layout.addWidget(lbl)
        layout.addStretch()

        estilo_boton = (
            "QPushButton { background: transparent; border: none; color: #8892a6; font-size: 11pt; }"
            "QPushButton:hover { background-color: #3a4358; border-radius: 4px; color: #d8dee9; }"
        )
        for texto, accion in [("—", self._minimizar), ("□", self._maximizar), ("✕", self._cerrar)]:
            btn = QPushButton(texto)
            btn.setFixedSize(34, 28)
            btn.setStyleSheet(estilo_boton)
            btn.clicked.connect(accion)
            layout.addWidget(btn)

    def _minimizar(self):
        self.ventana.showMinimized()

    def _maximizar(self):
        if self.ventana.isMaximized():
            self.ventana.showNormal()
        else:
            self.ventana.showMaximized()

    def _cerrar(self):
        self.ventana.close()

    def mousePressEvent(self, event):
        # En Wayland la app no puede reposicionarse a sí misma (move() no hace nada);
        # hay que pedirle el arrastre al compositor con startSystemMove(), que
        # también funciona en X11, así que es el único camino que sirve en los dos.
        if event.button() == Qt.LeftButton:
            handle = self.ventana.windowHandle()
            if handle is not None:
                handle.startSystemMove()

    def mouseDoubleClickEvent(self, event):
        self._maximizar()


RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
from bridge.config import cargar  # noqa: E402
from panel_perfiles import PanelPerfiles  # noqa: E402

VENTANA_S = 600  # muestra los últimos 10 min en la gráfica
VERSION = "1.2.0"


class MqttWorker(QObject):
    estado = Signal(str, str, str)  # alias, sufijo, payload

    def __init__(self, host: str, port: int, topic_prefix: str):
        super().__init__()
        self.topic_prefix = topic_prefix
        self.client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2)
        self.client.on_message = self._on_message
        self.client.connect(host, port)
        self.client.subscribe(f"{topic_prefix}/+/state/#")
        self.client.loop_start()

    def _on_message(self, client, userdata, msg):
        partes = msg.topic.split("/")
        if len(partes) < 4 or partes[0] != self.topic_prefix or partes[2] != "state":
            return
        alias = partes[1]
        sufijo = "/".join(partes[3:])
        self.estado.emit(alias, sufijo, msg.payload.decode())

    def publicar_cmd(self, alias: str, sufijo: str, payload: str) -> None:
        self.client.publish(f"{self.topic_prefix}/{alias}/cmd/{sufijo}", payload)


class VentanaPrincipal(QMainWindow):
    def __init__(self, cfg: dict):
        super().__init__()
        self.setWindowTitle(f"KiBarra v{VERSION}")
        self.setWindowFlags(Qt.FramelessWindowHint)
        self.cfg = cfg
        self.alias_actual: str | None = None
        self.historial: dict[str, list] = {"t": [], "v": [], "i": []}
        self.t0 = time.time()

        self.mqtt = MqttWorker(cfg["mqtt"]["host"], cfg["mqtt"]["port"], cfg["mqtt"]["topic_prefix"])
        self.mqtt.estado.connect(self._on_estado)

        self._construir_ui()

    # --- UI ---
    def _construir_ui(self):
        contenedor = QWidget()
        contenedor.setStyleSheet("background-color: #14181f; border: 1px solid #2a3040;")
        self.setCentralWidget(contenedor)
        layout_exterior = QVBoxLayout(contenedor)
        layout_exterior.setContentsMargins(0, 0, 0, 0)
        layout_exterior.setSpacing(0)
        layout_exterior.addWidget(BarraTitulo(self, f"KiBarra v{VERSION}"))

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(10, 10, 10, 10)
        layout_exterior.addWidget(central)

        # Selector de instrumento
        fila_top = QHBoxLayout()
        fila_top.addWidget(QLabel("Instrumento:"))
        self.combo_instrumento = QComboBox()
        self.combo_instrumento.addItems(sorted(self.cfg["instrumentos"].values()))
        self.combo_instrumento.currentTextChanged.connect(self._cambiar_instrumento)
        fila_top.addWidget(self.combo_instrumento)
        fila_top.addStretch()
        self.btn_toggle_perfiles = QPushButton("Perfiles ▸")
        self.btn_toggle_perfiles.setCheckable(True)
        self.btn_toggle_perfiles.clicked.connect(self._toggle_panel_perfiles)
        fila_top.addWidget(self.btn_toggle_perfiles)
        layout.addLayout(fila_top)

        # Lecturas en vivo: tarjetas grandes para lo importante
        fila_tarjetas = QHBoxLayout()
        self.tarjeta_v = TarjetaEstado("V medida", "voltios", "#f2c94c")
        self.tarjeta_i = TarjetaEstado("I medida", "amperios", "#56ccf2")
        self.tarjeta_p = TarjetaEstado("Potencia", "vatios", "#6fcf97")
        fila_tarjetas.addWidget(self.tarjeta_v)
        fila_tarjetas.addWidget(self.tarjeta_i)
        fila_tarjetas.addWidget(self.tarjeta_p)
        layout.addLayout(fila_tarjetas)

        grupo_detalle = QGroupBox("Consignas actuales en el Kiprim (referencia)")
        grid = QGridLayout(grupo_detalle)
        grid.setHorizontalSpacing(24)
        self.lbl_v_set = QLabel("—")
        self.lbl_i_set = QLabel("—")
        self.lbl_v_limit = QLabel("—")
        self.lbl_i_limit = QLabel("—")
        for col, (texto, lbl) in enumerate([
            ("V set", self.lbl_v_set),
            ("I set", self.lbl_i_set),
            ("V límite", self.lbl_v_limit),
            ("I límite (=OCP)", self.lbl_i_limit),
        ]):
            etiqueta = QLabel(texto)
            etiqueta.setStyleSheet("color: #6b7488; font-size: 9pt;")
            lbl.setStyleSheet("font-weight: 600; font-size: 11pt; color: #d8dee9;")
            grid.addWidget(etiqueta, 0, col)
            grid.addWidget(lbl, 1, col)
        layout.addWidget(grupo_detalle)

        # Controles manuales — campos puramente de envío, nunca repintados por la
        # telemetría (si no, cualquier valor a medio escribir se sobreescribe solo).
        grupo_ctrl = QGroupBox("Enviar consigna (SET) — corriente/tensión objetivo")
        fila_set = QHBoxLayout(grupo_ctrl)
        self.spin_voltage_set = SpinBoxSeleccionable()
        self.spin_voltage_set.setRange(0, 60)
        self.spin_voltage_set.setSuffix(" V")
        self.spin_voltage_set.setDecimals(3)
        self.spin_voltage_set.setValue(14.4)
        self.spin_current_set = SpinBoxSeleccionable()
        self.spin_current_set.setRange(0, 10)
        self.spin_current_set.setSuffix(" A")
        self.spin_current_set.setDecimals(3)
        self.spin_current_set.setValue(0.5)
        btn_aplicar_set = QPushButton("Enviar SET")
        btn_aplicar_set.setStyleSheet(
            "QPushButton { background-color: #2f6f4f; border: 1px solid #3f9f6f; } "
            "QPushButton:hover { background-color: #3f9f6f; }"
        )
        btn_aplicar_set.clicked.connect(self._aplicar_set)
        fila_set.addWidget(QLabel("Voltage:"))
        fila_set.addWidget(self.spin_voltage_set)
        fila_set.addWidget(QLabel("Current:"))
        fila_set.addWidget(self.spin_current_set)
        fila_set.addWidget(btn_aplicar_set)
        layout.addWidget(grupo_ctrl)

        grupo_limit = QGroupBox("Enviar límite (protección) — OVP/OCP")
        fila_limit = QHBoxLayout(grupo_limit)
        self.spin_voltage_limit = SpinBoxSeleccionable()
        self.spin_voltage_limit.setRange(0, 60)
        self.spin_voltage_limit.setSuffix(" V")
        self.spin_voltage_limit.setDecimals(3)
        self.spin_voltage_limit.setValue(15.0)
        self.spin_current_limit = SpinBoxSeleccionable()
        self.spin_current_limit.setRange(0, 10)
        self.spin_current_limit.setSuffix(" A")
        self.spin_current_limit.setDecimals(3)
        self.spin_current_limit.setValue(1.0)
        btn_aplicar_limit = QPushButton("Enviar LÍMITE")
        btn_aplicar_limit.setStyleSheet(
            "QPushButton { background-color: #8f3a3a; border: 1px solid #b34d4d; } "
            "QPushButton:hover { background-color: #b34d4d; }"
        )
        btn_aplicar_limit.clicked.connect(self._aplicar_limit)
        fila_limit.addWidget(QLabel("Voltage límite:"))
        fila_limit.addWidget(self.spin_voltage_limit)
        fila_limit.addWidget(QLabel("Current límite:"))
        fila_limit.addWidget(self.spin_current_limit)
        fila_limit.addWidget(btn_aplicar_limit)
        layout.addWidget(grupo_limit)

        fila_output = QHBoxLayout()
        self.btn_output = QPushButton("Salida OFF")
        self.btn_output.setCheckable(True)
        self.btn_output.setStyleSheet(
            "QPushButton { background-color: #3a1030; border: 1px solid #d000a0; color: #d8dee9; "
            "padding: 6px 16px; font-weight: 600; } "
            "QPushButton:hover { background-color: #4a1440; } "
            "QPushButton:checked { background-color: #d000a0; border-color: #ff33cc; color: white; }"
        )
        self.btn_output.clicked.connect(self._toggle_output)
        fila_output.addWidget(self.btn_output)
        fila_output.addSpacing(16)
        fila_output.addWidget(QLabel("Bridge:"))
        self.badge_conexion = Insignia("Sin datos")
        fila_output.addWidget(self.badge_conexion)
        fila_output.addSpacing(16)
        fila_output.addWidget(QLabel("Perfil:"))
        self.badge_perfil = Insignia("ninguno")
        fila_output.addWidget(self.badge_perfil)
        fila_output.addStretch()
        layout.addLayout(fila_output)

        self._ultimo_mensaje: float | None = None
        self._timer_conexion = QTimer(self)
        self._timer_conexion.timeout.connect(self._comprobar_conexion)
        self._timer_conexion.start(1000)

        # Gráfica en vivo
        self.plot = pg.PlotWidget(background="#1a1f29")
        self.plot.addLegend()
        self.plot.setLabel("bottom", "tiempo", "s")
        self.plot.setLabel("left", "V / A")
        self.curva_v = self.plot.plot([], [], pen="y", name="V medida")
        self.curva_i = self.plot.plot([], [], pen="c", name="I medida")
        layout.addWidget(self.plot)

        # Panel lateral de perfiles (dock, no modal, plegable) — empieza oculto,
        # antes de conectar la señal, para que el resize automático no se
        # dispare con el tamaño de ventana todavía sin fijar.
        self.panel_perfiles = PanelPerfiles(self.mqtt, carpeta_perfiles=str(RAIZ / "profiles"))
        self.addDockWidget(Qt.RightDockWidgetArea, self.panel_perfiles)
        # Llamar a hide() aquí no basta: como esto corre antes de ventana.show(),
        # el show() posterior de la ventana principal "revive" el dock igualmente.
        # Se difiere al siguiente ciclo del bucle de eventos, después de ese show().
        QTimer.singleShot(0, self.panel_perfiles.hide)

        self.resize(760, 800)
        if self.combo_instrumento.count():
            self._cambiar_instrumento(self.combo_instrumento.currentText())

        # el panel "emerge" ensanchando la ventana, sin encoger el contenido principal
        self.panel_perfiles.visibilityChanged.connect(self._on_panel_perfiles_visibility)

    # --- lógica ---
    def _toggle_panel_perfiles(self):
        self.panel_perfiles.setVisible(self.btn_toggle_perfiles.isChecked())

    def _on_panel_perfiles_visibility(self, visible: bool):
        self.btn_toggle_perfiles.setChecked(visible)
        ancho = self.panel_perfiles.minimumWidth()
        if visible:
            self.resize(self.width() + ancho, self.height())
        else:
            self.resize(max(self.width() - ancho, 760), self.height())

    def _cambiar_instrumento(self, alias: str):
        self.alias_actual = alias
        self.panel_perfiles.set_alias(alias)
        self.historial = {"t": [], "v": [], "i": []}
        self.t0 = time.time()
        self._ultimo_mensaje = None
        self.badge_conexion.set_estado("Sin datos", False, color_inactivo="#8f3a3a")

    def _comprobar_conexion(self):
        if self._ultimo_mensaje is None or time.time() - self._ultimo_mensaje > 3.0:
            self.badge_conexion.set_estado("Desconectado", False, color_inactivo="#8f3a3a")
        else:
            self.badge_conexion.set_estado("Conectado", True)

    def _on_estado(self, alias: str, sufijo: str, payload: str):
        if alias != self.alias_actual:
            return
        self._ultimo_mensaje = time.time()
        if sufijo == "voltage_measured":
            self.tarjeta_v.actualizar(f"{float(payload):.2f}")
            self._acumular("v", float(payload))
            self.panel_perfiles.actualizar_estado(sufijo, payload)
        elif sufijo == "current_measured":
            self.tarjeta_i.actualizar(f"{float(payload):.3f}")
            self._acumular("i", float(payload))
            self.panel_perfiles.actualizar_estado(sufijo, payload)
        elif sufijo == "power_measured":
            self.tarjeta_p.actualizar(f"{float(payload):.2f}")
        elif sufijo == "output":
            self.btn_output.setChecked(payload == "ON")
            self.btn_output.setText(f"Salida {payload}")
        elif sufijo == "profile":
            self.badge_perfil.set_estado(payload, payload != "ninguno")
            self.panel_perfiles.actualizar_estado(sufijo, payload)
        elif sufijo in ("profile_step", "profile_condicion", "profile_condicion_valor",
                        "profile_tiempo_restante"):
            self.panel_perfiles.actualizar_estado(sufijo, payload)
        elif sufijo == "voltage_set":
            self.lbl_v_set.setText(f"{float(payload):.3f}")
        elif sufijo == "current_set":
            self.lbl_i_set.setText(f"{float(payload):.3f}")
        elif sufijo == "voltage_limit":
            self.lbl_v_limit.setText(f"{float(payload):.3f}")
        elif sufijo == "current_limit":
            self.lbl_i_limit.setText(f"{float(payload):.3f}")

    def _acumular(self, canal: str, valor: float):
        t = time.time() - self.t0
        self.historial["t"].append(t)
        self.historial.setdefault(canal, []).append(valor)
        # recorte a la ventana visible
        corte = t - VENTANA_S
        while self.historial["t"] and self.historial["t"][0] < corte:
            self.historial["t"].pop(0)
            if self.historial["v"]:
                self.historial["v"].pop(0)
            if self.historial["i"]:
                self.historial["i"].pop(0)
        n = min(len(self.historial["t"]), len(self.historial["v"]), len(self.historial["i"]))
        if n > 1:
            self.curva_v.setData(self.historial["t"][-n:], self.historial["v"][-n:])
            self.curva_i.setData(self.historial["t"][-n:], self.historial["i"][-n:])

    def _aplicar_set(self):
        if not self.alias_actual:
            return
        self.mqtt.publicar_cmd(self.alias_actual, "voltage", f"{self.spin_voltage_set.value():.3f}")
        self.mqtt.publicar_cmd(self.alias_actual, "current", f"{self.spin_current_set.value():.3f}")

    def _aplicar_limit(self):
        if not self.alias_actual:
            return
        self.mqtt.publicar_cmd(self.alias_actual, "voltage_limit", f"{self.spin_voltage_limit.value():.3f}")
        self.mqtt.publicar_cmd(self.alias_actual, "current_limit", f"{self.spin_current_limit.value():.3f}")

    def _toggle_output(self):
        if not self.alias_actual:
            return
        self.mqtt.publicar_cmd(self.alias_actual, "output", "ON" if self.btn_output.isChecked() else "OFF")

def main():
    ruta_config = sys.argv[1] if len(sys.argv) > 1 else str(RAIZ / "config.yaml")
    cfg = cargar(ruta_config)
    app = QApplication(sys.argv)
    app.setStyleSheet(ESTILO)
    app.setWindowIcon(QIcon.fromTheme("kiprim-gui"))
    ventana = VentanaPrincipal(cfg)
    ventana.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
