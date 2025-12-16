# src/widgets_parts/custom_product_dialog.py
from __future__ import annotations

from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLineEdit,
    QPushButton,
    QMessageBox,
)


class CustomProductDialog(QDialog):
    """Diálogo para agregar un producto personalizado o servicio."""

    def __init__(self, parent=None, app_icon: QIcon = QIcon()):
        super().__init__(parent)
        self.setWindowTitle("Agregar producto personalizado o servicio")
        self.resize(420, 260)
        if not app_icon.isNull():
            self.setWindowIcon(app_icon)
        self.resultado = None

        form = QFormLayout(self)
        self.edCodigo = QLineEdit()
        self.edCodigo.setPlaceholderText("Ej: SRV001 o PERS001")
        self.edNombre = QLineEdit()
        self.edNombre.setPlaceholderText("Nombre del producto/servicio")
        self.edObs = QLineEdit()
        self.edObs.setPlaceholderText("Observación (opcional)")
        self.edPrecio = QLineEdit()
        self.edPrecio.setPlaceholderText("Precio unitario")
        self.edPrecio.setText("0.00")
        self.edCant = QLineEdit()
        self.edCant.setPlaceholderText("Cantidad")
        self.edCant.setText("1")

        form.addRow("Código:", self.edCodigo)
        form.addRow("Nombre:", self.edNombre)
        form.addRow("Observación:", self.edObs)
        form.addRow("Precio:", self.edPrecio)
        form.addRow("Cantidad:", self.edCant)

        btnGuardar = QPushButton("Guardar")
        btnGuardar.clicked.connect(self._guardar)
        form.addRow(btnGuardar)

    def _guardar(self):
        from ..utils import to_float

        codigo = self.edCodigo.text().strip()
        nombre = self.edNombre.text().strip()
        obs = self.edObs.text().strip()
        precio = to_float(self.edPrecio.text(), 0.0)
        cant = to_float(self.edCant.text(), 1.0)

        if not codigo:
            QMessageBox.warning(self, "Falta código", "Ingrese un código para el producto personalizado.")
            return
        if not nombre:
            QMessageBox.warning(self, "Falta nombre", "Ingrese un nombre para el producto personalizado.")
            return
        if precio < 0:
            QMessageBox.warning(self, "Precio inválido", "El precio no puede ser negativo.")
            return
        if cant <= 0:
            QMessageBox.warning(self, "Cantidad inválida", "La cantidad debe ser mayor que 0.")
            return

        try:
            cant = int(round(float(cant)))
            if cant <= 0:
                cant = 1
        except Exception:
            cant = 1

        self.resultado = {
            "codigo": codigo,
            "nombre": nombre,
            "observacion": obs,
            "precio": float(precio),
            "cantidad": int(cant),
        }
        self.accept()
