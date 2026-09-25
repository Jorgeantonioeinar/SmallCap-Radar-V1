"""
live_log.py
-----------
Handler de logging que guarda las últimas N líneas en memoria, para
mostrarlas en la "Consola de Eventos en Vivo" del dashboard de Streamlit
(st.expander con los logs recientes del bot).
"""

import logging
from collections import deque

MAX_LINES = 200
_buffer = deque(maxlen=MAX_LINES)


class InMemoryLogHandler(logging.Handler):
    def emit(self, record):
        try:
            _buffer.append(self.format(record))
        except Exception:
            pass


def attach_once():
    """Conecta el handler a la raíz de logging una sola vez (evita duplicados en reruns de Streamlit)."""
    root = logging.getLogger()
    already_attached = any(isinstance(h, InMemoryLogHandler) for h in root.handlers)
    if not already_attached:
        handler = InMemoryLogHandler()
        handler.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(name)s: %(message)s", "%H:%M:%S"))
        handler.setLevel(logging.INFO)
        root.addHandler(handler)
        root.setLevel(logging.INFO)


def get_recent_lines(n: int = 50):
    return list(_buffer)[-n:]
