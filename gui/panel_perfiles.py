"""Panel lateral (dock, no modal) para editar y ejecutar perfiles de carga."""
from __future__ import annotations

from pathlib import Path

import yaml
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QDockWidget, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QPushButton, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget,
)

COND_FIN = "Fin del perfil (sin condición)"
CONDICIONES = [
    COND_FIN,
    "Tiempo transcurrido (s)",
    "Corriente medida baja de... (A)",
    "Corriente medida sube de... (A)",
    "Voltaje medido baja de... (V)",
    "Voltaje medido sube de... (V)",
]
CLAVE_CONDICION = {
    "Tiempo transcurrido (s)": "tiempo",
    "Corriente medida baja de... (A)": "corriente_medida_bajo",
    "Corriente medida sube de... (A)": "corriente_medida_alto",
    "Voltaje medido baja de... (V)": "voltage_medido_bajo",
    "Voltaje medido sube de... (V)": "voltage_medido_alto",
}
COLUMNAS = [
    ("Voltage\nSET (V)", "voltage", "Tensión objetivo (SCPI 'voltage'). Vacío = no tocarla."),
    ("Current\nSET (A)", "current", "Corriente objetivo directa (SCPI 'current'), la que de verdad regula la carga. Vacío = no tocarla."),
    ("Voltage\nlímite (V)", "voltage_limit", "Techo de protección de tensión (SCPI 'voltage:limit'). Vacío = no tocarlo."),
    ("Current\nlímite (A) = OCP", "current_limit", "Techo de protección de corriente (SCPI 'current:limit') = OCP en esta fuente. Vacío = no tocarlo."),
    ("Forzar\nsalida", "output", "Vacío = no tocar la salida. ON/OFF para forzarla (típico: OFF en el último paso)."),
    ("Condición para\npasar al siguiente paso", "hasta", "Qué tiene que pasar para avanzar: tiempo transcurrido, o que la corriente/voltaje medidos crucen un valor."),
    ("Valor de\nla condición", "valor", "El número de la condición elegida (segundos, amperios o voltios)."),
]


class PanelPerfiles(QDockWidget):
    def __init__(self, mqtt_worker, carpeta_perfiles: str = "profiles"):
        super().__init__("Perfiles programados")
        self.mqtt = mqtt_worker
        self.alias_actual: str | None = None
        self._nombre_activo = "ninguno"
        self._paso_activo = -1
        self.carpeta = Path(carpeta_perfiles)
        self.carpeta.mkdir(exist_ok=True)
        # Sin DockWidgetFloatable a propósito: al desacoplarlo como ventana
        # independiente, el ancho reportado deja de coincidir con el que se usa
        # para encoger/agrandar la ventana principal, y esta crece sin parar
        # en cada ciclo mostrar/ocultar. No era el uso previsto (panel lateral
        # acoplado), así que se quita la opción en vez de parchear el cálculo.
        self.setFeatures(QDockWidget.DockWidgetMovable | QDockWidget.DockWidgetClosable)
        self.setMinimumWidth(460)

        contenido = QWidget()
        layout = QVBoxLayout(contenido)

        # --- banner de estado: línea propia, arriba del todo ---
        self.lbl_estado_perfil = QLabel()
        self.lbl_estado_perfil.setAlignment(Qt.AlignCenter)
        layout.addWidget(self.lbl_estado_perfil)
        self.lbl_progreso = QLabel()
        self.lbl_progreso.setAlignment(Qt.AlignCenter)
        self.lbl_progreso.setStyleSheet("color: #8892a6; font-size: 10pt;")
        layout.addWidget(self.lbl_progreso)
        self._pintar_banner(activo=False)

        # --- botones de ejecución, debajo del banner ---
        grupo_exec = QHBoxLayout()
        btn_arrancar = QPushButton("▶ Arrancar")
        btn_arrancar.clicked.connect(self._arrancar)
        btn_parar = QPushButton("■ Parar")
        btn_parar.clicked.connect(self._parar)
        grupo_exec.addWidget(btn_arrancar)
        grupo_exec.addWidget(btn_parar)
        layout.addLayout(grupo_exec)

        # --- selector / cargar / nuevo ---
        fila_top = QHBoxLayout()
        fila_top.addWidget(QLabel("Perfil:"))
        self.combo_existentes = QComboBox()
        self._recargar_lista()
        fila_top.addWidget(self.combo_existentes)
        btn_cargar = QPushButton("Cargar en editor")
        btn_cargar.clicked.connect(self._cargar_seleccionado)
        fila_top.addWidget(btn_cargar)
        btn_nuevo = QPushButton("Nuevo")
        btn_nuevo.clicked.connect(self._nuevo)
        fila_top.addWidget(btn_nuevo)
        layout.addLayout(fila_top)

        fila_nombre = QHBoxLayout()
        fila_nombre.addWidget(QLabel("Nombre:"))
        self.campo_nombre = QLineEdit()
        fila_nombre.addWidget(self.campo_nombre)
        layout.addLayout(fila_nombre)

        self.tabla = QTableWidget(0, len(COLUMNAS))
        self.tabla.setHorizontalHeaderLabels([c[0] for c in COLUMNAS])
        for col, (_, _, tooltip) in enumerate(COLUMNAS):
            self.tabla.horizontalHeaderItem(col).setToolTip(tooltip)
        self.tabla.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.tabla.horizontalHeader().setMinimumHeight(44)
        layout.addWidget(self.tabla)

        nota = QLabel(
            "'SET' es lo que de verdad manda la fuente en modo CC; 'límite' es solo un techo de protección."
        )
        nota.setStyleSheet("color: #8892a6; font-size: 9pt;")
        nota.setWordWrap(True)
        layout.addWidget(nota)

        fila_botones = QHBoxLayout()
        btn_add = QPushButton("+ Paso")
        btn_add.clicked.connect(self._anadir_paso)
        btn_del = QPushButton("- Paso seleccionado")
        btn_del.clicked.connect(self._quitar_paso)
        fila_botones.addWidget(btn_add)
        fila_botones.addWidget(btn_del)
        fila_botones.addStretch()
        btn_guardar = QPushButton("Guardar")
        btn_guardar.clicked.connect(self._guardar)
        fila_botones.addWidget(btn_guardar)
        layout.addLayout(fila_botones)

        self.lbl_confirmacion = QLabel("")
        self.lbl_confirmacion.setStyleSheet("font-weight: 600;")
        layout.addWidget(self.lbl_confirmacion)

        self.setWidget(contenido)

        if self.combo_existentes.count():
            self._cargar_seleccionado()
        else:
            self._anadir_paso()

    # --- integración con la ventana principal ---
    def set_alias(self, alias: str | None) -> None:
        self.alias_actual = alias

    def actualizar_estado(self, sufijo: str, payload: str) -> None:
        atributo = {
            "profile": "_nombre_activo",
            "profile_step": "_paso_activo",
            "profile_condicion": "_condicion_tipo",
            "profile_condicion_valor": "_condicion_valor",
            "profile_tiempo_restante": "_tiempo_restante",
            "voltage_measured": "_v_medida",
            "current_measured": "_i_medida",
        }.get(sufijo)
        if atributo is None:
            return
        setattr(self, atributo, payload)

        activo = getattr(self, "_nombre_activo", "ninguno") != "ninguno"
        if not activo:
            self.lbl_estado_perfil.setText("Ningún perfil en marcha")
            self.lbl_progreso.setText("")
        else:
            paso = int(getattr(self, "_paso_activo", -1)) + 1
            self.lbl_estado_perfil.setText(f"● En marcha: {self._nombre_activo} — paso {paso}")
            self.lbl_progreso.setText(self._texto_progreso())
        self._pintar_banner(activo)

    def _texto_progreso(self) -> str:
        tipo = getattr(self, "_condicion_tipo", "")
        if not tipo:
            return ""
        if tipo == "tiempo":
            try:
                s = float(getattr(self, "_tiempo_restante", ""))
            except (TypeError, ValueError):
                return ""
            mm, ss = divmod(int(s), 60)
            return f"⏱ Quedan {mm:02d}:{ss:02d}"
        etiquetas = {
            "corriente_medida_bajo": ("I medida", "_i_medida", "A", "baje de"),
            "corriente_medida_alto": ("I medida", "_i_medida", "A", "suba de"),
            "voltage_medido_bajo": ("V medida", "_v_medida", "V", "baje de"),
            "voltage_medido_alto": ("V medida", "_v_medida", "V", "suba de"),
        }
        if tipo not in etiquetas:
            return ""
        nombre, atributo_medida, unidad, verbo = etiquetas[tipo]
        try:
            medido = float(getattr(self, atributo_medida, ""))
            objetivo = float(getattr(self, "_condicion_valor", ""))
        except (TypeError, ValueError):
            return ""
        faltan = abs(objetivo - medido)
        return (f"{nombre}: {medido:.3f} {unidad} — hasta que {verbo} {objetivo:.3f} {unidad} "
                f"(faltan {faltan:.3f})")

    def _pintar_banner(self, activo: bool) -> None:
        if not self.lbl_estado_perfil.text():
            self.lbl_estado_perfil.setText("Ningún perfil en marcha")
        if activo:
            self.lbl_estado_perfil.setStyleSheet(
                "background-color: #1f3d2c; color: #6fcf97; font-weight: 700; "
                "font-size: 12pt; border: 1px solid #2f6f4f; border-radius: 8px; padding: 10px;"
            )
        else:
            self.lbl_estado_perfil.setStyleSheet(
                "background-color: #1c212b; color: #8892a6; font-weight: 600; "
                "font-size: 11pt; border: 1px solid #2a3040; border-radius: 8px; padding: 10px;"
            )

    def _mensaje(self, texto: str, ok: bool = True) -> None:
        color = "#6fcf97" if ok else "#eb5757"
        self.lbl_confirmacion.setStyleSheet(f"font-weight: 600; color: {color};")
        self.lbl_confirmacion.setText(("✓ " if ok else "✗ ") + texto)
        QTimer.singleShot(5000, lambda: self.lbl_confirmacion.setText(""))

    def _arrancar(self):
        if not self.alias_actual or not self.combo_existentes.currentText():
            self._mensaje("Selecciona antes un perfil.", ok=False)
            return
        self.mqtt.publicar_cmd(self.alias_actual, "profile/start", self.combo_existentes.currentText())

    def _parar(self):
        if not self.alias_actual:
            return
        self.mqtt.publicar_cmd(self.alias_actual, "profile/stop", "")

    # --- helpers de fila ---
    def _combo_output(self, valor: str = "") -> QComboBox:
        combo = QComboBox()
        combo.addItems(["", "ON", "OFF"])
        combo.setToolTip(COLUMNAS[4][2])
        if valor:
            combo.setCurrentText(valor)
        return combo

    def _combo_condicion(self, valor: str = COND_FIN) -> QComboBox:
        combo = QComboBox()
        combo.addItems(CONDICIONES)
        combo.setToolTip(COLUMNAS[5][2])
        combo.setCurrentText(valor)
        return combo

    def _anadir_paso(self):
        fila = self.tabla.rowCount()
        self.tabla.insertRow(fila)
        for col in range(4):
            self.tabla.setItem(fila, col, QTableWidgetItem(""))
        self.tabla.setCellWidget(fila, 4, self._combo_output())
        self.tabla.setCellWidget(fila, 5, self._combo_condicion())
        self.tabla.setItem(fila, 6, QTableWidgetItem(""))

    def _quitar_paso(self):
        fila = self.tabla.currentRow()
        if fila >= 0:
            self.tabla.removeRow(fila)

    def _recargar_lista(self):
        actual = self.combo_existentes.currentText()
        self.combo_existentes.clear()
        self.combo_existentes.addItems(sorted(p.stem for p in self.carpeta.glob("*.yaml")))
        if actual:
            self.combo_existentes.setCurrentText(actual)

    def _nuevo(self):
        self.campo_nombre.clear()
        self.tabla.setRowCount(0)
        self._anadir_paso()

    def _cargar_seleccionado(self):
        nombre = self.combo_existentes.currentText()
        if not nombre:
            return
        ruta = self.carpeta / f"{nombre}.yaml"
        with open(ruta, encoding="utf-8") as f:
            datos = yaml.safe_load(f)
        self.campo_nombre.setText(datos.get("nombre", nombre))
        self.tabla.setRowCount(0)
        for paso in datos.get("pasos", []):
            fila = self.tabla.rowCount()
            self.tabla.insertRow(fila)
            for col, clave in [(0, "voltage"), (1, "current"), (2, "voltage_limit"), (3, "current_limit")]:
                valor = paso.get(clave, "")
                self.tabla.setItem(fila, col, QTableWidgetItem("" if valor == "" else str(valor)))
            output_val = paso.get("output", "")
            output_val = str(output_val).upper() if output_val != "" else ""
            self.tabla.setCellWidget(fila, 4, self._combo_output(output_val))
            hasta = paso.get("hasta")
            if not hasta:
                self.tabla.setCellWidget(fila, 5, self._combo_condicion(COND_FIN))
                self.tabla.setItem(fila, 6, QTableWidgetItem(""))
            else:
                clave, valor = next(iter(hasta.items()))
                texto = {v: k for k, v in CLAVE_CONDICION.items()}.get(clave, COND_FIN)
                self.tabla.setCellWidget(fila, 5, self._combo_condicion(texto))
                self.tabla.setItem(fila, 6, QTableWidgetItem(str(valor)))
        self._mensaje(f"Perfil '{nombre}' cargado en el editor.")

    def _guardar(self):
        nombre = self.campo_nombre.text().strip()
        if not nombre:
            self._mensaje("Ponle un nombre al perfil antes de guardar.", ok=False)
            return
        try:
            pasos = []
            for fila in range(self.tabla.rowCount()):
                paso = {}
                for col, clave in [(0, "voltage"), (1, "current"), (2, "voltage_limit"), (3, "current_limit")]:
                    txt = self._texto_celda(fila, col)
                    if txt:
                        paso[clave] = float(txt)
                combo_out = self.tabla.cellWidget(fila, 4)
                if combo_out.currentText():
                    paso["output"] = combo_out.currentText()
                combo_cond = self.tabla.cellWidget(fila, 5)
                cond_texto = combo_cond.currentText()
                if cond_texto != COND_FIN:
                    valor_txt = self._texto_celda(fila, 6)
                    if not valor_txt:
                        self._mensaje(f"El paso {fila + 1} tiene condición sin valor.", ok=False)
                        return
                    paso["hasta"] = {CLAVE_CONDICION[cond_texto]: float(valor_txt)}
                pasos.append(paso)

            if not pasos:
                self._mensaje("Añade al menos un paso.", ok=False)
                return

            datos = {"nombre": nombre, "pasos": pasos}
            ruta = self.carpeta / f"{nombre}.yaml"
            with open(ruta, "w", encoding="utf-8") as f:
                yaml.safe_dump(datos, f, allow_unicode=True, sort_keys=False)
        except (OSError, ValueError) as exc:
            self._mensaje(f"No se pudo guardar: {exc}", ok=False)
            return

        self._recargar_lista()
        self.combo_existentes.setCurrentText(nombre)
        self._mensaje("Cambio realizado con éxito.")

    def _texto_celda(self, fila: int, col: int) -> str:
        item = self.tabla.item(fila, col)
        return item.text().strip() if item else ""
