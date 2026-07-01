"""
ECGDisplay — scrolling 12-lead ECG display widget.

Renders a scrolling ECG paper view using pyqtgraph's hardware-accelerated
line plotting.  Maintains an internal ring buffer so that the most recent
``window_sec`` seconds of data are always visible.

Layout
------
12 stacked :class:`pyqtgraph.PlotItem` widgets, one per lead, all sharing
a common x-axis.  Lead labels appear on the left y-axis of each row.  The
time axis is shown only on the bottom row.

Performance notes
-----------------
* The ring buffer is a pre-allocated ``float32`` numpy array.  No memory
  allocation occurs during normal operation.
* ``np.roll`` across the full 2-D buffer (single call) reorders data for
  display; at 500 Hz / 10 s / 12 leads this is a ~240 KB memcpy — fast.
* pyqtgraph uses OpenGL (when available) or a QPainter fallback.  Both are
  fast enough for 12 curves at 10 fps.
"""

from __future__ import annotations

import logging

import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Visual constants — ECG paper style
# ---------------------------------------------------------------------------

_BACKGROUND_RGB = (15, 15, 15)          # Near-black
_GRID_ALPHA = 0.25
_TRACE_COLOR = (0, 200, 80)             # Green — classic monitor style
_TRACE_WIDTH = 1

LEAD_NAMES: tuple[str, ...] = (
    "I", "II", "III",
    "aVR", "aVL", "aVF",
    "V1", "V2", "V3", "V4", "V5", "V6",
)


class ECGDisplay(QWidget):
    """
    Scrolling 12-lead ECG display widget.

    Parameters
    ----------
    sample_rate:
        Expected sample rate of incoming data [Hz].  Used to build the
        x-axis time vector.
    window_sec:
        Width of the visible scrolling window [s].
    parent:
        Optional Qt parent widget.
    """

    def __init__(
        self,
        sample_rate: float = 500.0,
        window_sec: float = 10.0,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._sample_rate = sample_rate
        self._window_sec = window_sec
        self._buffer_size = int(sample_rate * window_sec)

        # Pre-allocated ring buffer: rows = samples, columns = leads
        self._buffer = np.zeros((self._buffer_size, 12), dtype=np.float32)
        self._write_pos: int = 0

        # Pre-computed x-axis (recomputed only when rate/window change)
        self._x_axis = np.linspace(0.0, window_sec, self._buffer_size)

        self._curves: list[pg.PlotDataItem] = []
        self._plots: list[pg.PlotItem] = []

        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        pg.setConfigOption("background", _BACKGROUND_RGB)
        pg.setConfigOption("foreground", (200, 200, 200))
        pg.setConfigOption("antialias", False)   # Faster without AA for many pts

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._graphics = pg.GraphicsLayoutWidget()
        layout.addWidget(self._graphics)

        pen = pg.mkPen(color=_TRACE_COLOR, width=_TRACE_WIDTH)

        for i, name in enumerate(LEAD_NAMES):
            plot = self._graphics.addPlot(row=i, col=0)
            self._apply_plot_style(plot, name, is_bottom=(i == len(LEAD_NAMES) - 1))

            # Link all x-axes to the first plot for synchronised scrolling
            if self._plots:
                plot.setXLink(self._plots[0])

            curve = plot.plot(self._x_axis, self._buffer[:, i], pen=pen)
            self._curves.append(curve)
            self._plots.append(plot)

    def _apply_plot_style(
        self, plot: pg.PlotItem, lead_name: str, is_bottom: bool
    ) -> None:
        """Configure plot appearance to match an ECG monitor display."""
        plot.setLabel("left", lead_name, **{"font-size": "8pt"})
        plot.setMouseEnabled(x=False, y=False)
        plot.setMenuEnabled(False)
        plot.hideButtons()
        plot.setYRange(-1.5, 1.5, padding=0)
        plot.showGrid(x=True, y=True, alpha=_GRID_ALPHA)

        # Only the bottom row gets a labelled x-axis
        if is_bottom:
            plot.setLabel("bottom", "Time (s)")
        else:
            plot.hideAxis("bottom")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def update_ecg_data(self, batch: np.ndarray) -> None:
        """
        Append a batch of ECG samples and refresh the display.

        Parameters
        ----------
        batch:
            Shape ``(N, 12)`` — *N* new samples across 12 leads [mV].
            Called from the GUI thread via a Qt signal connection.
        """
        n = batch.shape[0]
        end = self._write_pos + n

        if end <= self._buffer_size:
            self._buffer[self._write_pos:end] = batch
        else:
            # Wrap around the ring buffer
            split = self._buffer_size - self._write_pos
            self._buffer[self._write_pos:] = batch[:split]
            self._buffer[:n - split] = batch[split:]

        self._write_pos = end % self._buffer_size
        self._refresh_display()

    def clear(self) -> None:
        """Reset the display to a flat line."""
        self._buffer[:] = 0.0
        self._write_pos = 0
        self._refresh_display()

    def set_sample_rate(self, sample_rate: float) -> None:
        """Reconfigure buffer and x-axis for a new sample rate."""
        self._sample_rate = sample_rate
        self._reallocate_buffer()

    def set_window_seconds(self, seconds: float) -> None:
        """Change the visible scrolling window width."""
        self._window_sec = seconds
        self._reallocate_buffer()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _reallocate_buffer(self) -> None:
        self._buffer_size = int(self._sample_rate * self._window_sec)
        self._buffer = np.zeros((self._buffer_size, 12), dtype=np.float32)
        self._write_pos = 0
        self._x_axis = np.linspace(0.0, self._window_sec, self._buffer_size)

    def _refresh_display(self) -> None:
        """Rotate the ring buffer so the oldest sample is leftmost, then redraw."""
        pos = self._write_pos
        # Single 2-D roll is cheaper than 12 individual 1-D rolls
        ordered = np.roll(self._buffer, -pos, axis=0)
        for i, curve in enumerate(self._curves):
            curve.setData(self._x_axis, ordered[:, i])
