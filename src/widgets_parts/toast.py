# src/widgets_parts/toast.py
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, QEvent, QPropertyAnimation
from PySide6.QtWidgets import (
    QFrame, QLabel, QHBoxLayout, QWidget, QGraphicsOpacityEffect
)


class Toast(QFrame):
    """
    Notificación flotante tipo "toast":
    - No roba foco
    - No intercepta mouse (no afecta la interacción)
    - Se auto-cierra tras duration_ms (con fade-out)
    - Se posiciona arriba a la derecha del window() del parent
    - Reutilizable en cualquier formulario
    """

    def __init__(
        self,
        parent: QWidget,
        message: str,
        *,
        duration_ms: int = 5000,
        fade_ms: int = 300,
        margin_px: int = 16,
        gap_px: int = 8,
        max_width: int = 360,
    ):
        super().__init__(None)  # top-level, para que flote sobre la ventana

        self._host = parent
        self._anchor = parent.window() if parent is not None else None
        self._margin = int(margin_px)
        self._gap = int(gap_px)
        self._fade_ms = int(fade_ms)
        self._closing = False

        flags = Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
        try:
            flags |= Qt.WindowDoesNotAcceptFocus
        except Exception:
            pass
        self.setWindowFlags(flags)

        # no bloquea interacción
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFocusPolicy(Qt.NoFocus)

        self.setObjectName("Toast")
        self.setStyleSheet(
            """
            QFrame#Toast {
                background: rgba(25, 25, 25, 220);
                border: 1px solid rgba(255,255,255,40);
                border-radius: 10px;
            }
            QLabel {
                color: white;
                font-size: 12px;
            }
            """
        )

        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 10, 12, 10)
        lay.setSpacing(8)

        self.lbl = QLabel(message)
        self.lbl.setWordWrap(True)
        self.lbl.setMaximumWidth(int(max_width))
        lay.addWidget(self.lbl)

        # Opacidad (para fade)
        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(1.0)
        self.setGraphicsEffect(self._opacity)

        self._fade_anim = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade_anim.setDuration(self._fade_ms)
        self._fade_anim.setStartValue(1.0)
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._final_close)

        self.adjustSize()

        # reposicionar si mueven / redimensionan la ventana
        if self._anchor is not None:
            try:
                self._anchor.installEventFilter(self)
            except Exception:
                pass

        self._reposition()

        # en vez de close() directo, hacemos fade
        QTimer.singleShot(int(duration_ms), self.fade_out)

    @classmethod
    def notify(
        cls,
        parent: QWidget,
        message: str,
        *,
        duration_ms: int = 5000,
        fade_ms: int = 300,
        margin_px: int = 16,
        gap_px: int = 8,
        max_width: int = 360,
    ) -> "Toast":
        t = cls(
            parent,
            message,
            duration_ms=duration_ms,
            fade_ms=fade_ms,
            margin_px=margin_px,
            gap_px=gap_px,
            max_width=max_width,
        )

        # mantener referencia (evitar GC) y permitir stacking
        host = parent.window() if parent is not None else None
        if host is not None:
            lst = getattr(host, "_active_toasts", None)
            if lst is None:
                lst = []
                setattr(host, "_active_toasts", lst)
            lst.append(t)

        QFrame.show(t)
        t._reposition_all()
        return t

    # =============================
    # Fade-out
    # =============================
    def fade_out(self):
        if self._closing:
            return
        self._closing = True
        try:
            # por si ya estaba en otra opacidad
            self._fade_anim.stop()
            self._fade_anim.setStartValue(float(self._opacity.opacity()))
            self._fade_anim.setEndValue(0.0)
            self._fade_anim.start()
        except Exception:
            # fallback: cerrar directo
            self._final_close()

    def _final_close(self):
        # cierre real
        try:
            QFrame.close(self)
        except Exception:
            try:
                self.close()
            except Exception:
                pass

    # =============================
    # Stacking / posicionamiento
    # =============================
    def _reposition_all(self):
        host = self._anchor
        if host is None:
            return
        try:
            lst = getattr(host, "_active_toasts", []) or []
            alive = [x for x in lst if x is not None and not x.isHidden()]
            setattr(host, "_active_toasts", alive)

            for i, toast in enumerate(alive):
                toast._reposition(stack_index=i)
        except Exception:
            pass

    def _reposition(self, stack_index: int = 0):
        if self._anchor is None:
            return
        try:
            self.adjustSize()
            geo = self._anchor.frameGeometry()
            x = geo.right() - self.width() - self._margin
            y = geo.top() + self._margin + stack_index * (self.height() + self._gap)
            self.move(x, y)
        except Exception:
            pass

    def eventFilter(self, obj, event):
        try:
            if obj is self._anchor and event.type() in (QEvent.Move, QEvent.Resize, QEvent.Show):
                self._reposition_all()
        except Exception:
            pass
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        # limpiar event filter y lista
        try:
            if self._anchor is not None:
                self._anchor.removeEventFilter(self)
        except Exception:
            pass

        try:
            host = self._anchor
            if host is not None and hasattr(host, "_active_toasts"):
                lst = getattr(host, "_active_toasts", []) or []
                if self in lst:
                    lst.remove(self)
                setattr(host, "_active_toasts", lst)
                for i, toast in enumerate(lst):
                    try:
                        toast._reposition(stack_index=i)
                    except Exception:
                        pass
        except Exception:
            pass

        return super().closeEvent(event)
