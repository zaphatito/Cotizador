from __future__ import annotations

from collections.abc import Sequence

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtWidgets import QHeaderView, QTableView


def bounded_column_widths(
    widths: Sequence[int],
    *,
    available: int,
    minimum: int,
    changed_index: int | None = None,
    fill_index: int | None = None,
) -> list[int]:
    """Redistribuye anchos para ocupar, sin superar, el viewport disponible."""

    result = [max(1, int(value)) for value in widths]
    if not result or int(available) <= 0:
        return result
    available = int(available)
    effective_minimum = max(1, min(int(minimum), available // len(result)))
    result = [max(effective_minimum, value) for value in result]

    excess = sum(result) - available
    if excess > 0:
        other_indexes = [
            index for index in range(len(result)) if index != changed_index
        ]
        groups = [other_indexes]
        if changed_index is not None and 0 <= changed_index < len(result):
            groups.append([changed_index])
        for indexes in groups:
            while excess > 0:
                capacities = {
                    index: result[index] - effective_minimum
                    for index in indexes
                    if result[index] > effective_minimum
                }
                if not capacities:
                    break
                capacity_total = sum(capacities.values())
                for index, capacity in capacities.items():
                    share = max(1, round(excess * capacity / capacity_total))
                    reduction = min(capacity, excess, share)
                    result[index] -= reduction
                    excess -= reduction
                    if excess <= 0:
                        break
            if excess <= 0:
                break

    spare = available - sum(result)
    if spare > 0:
        candidates = [index for index in range(len(result)) if index != changed_index]
        if not candidates:
            candidates = (
                [changed_index]
                if changed_index is not None and 0 <= changed_index < len(result)
                else [0]
            )
        weight_total = sum(result[index] for index in candidates)
        remaining = spare
        ordered = list(candidates)
        if fill_index in ordered:
            ordered.remove(fill_index)
            ordered.append(int(fill_index))
        for position, index in enumerate(ordered):
            if position == len(ordered) - 1:
                addition = remaining
            else:
                addition = min(
                    remaining,
                    round(spare * result[index] / max(1, weight_total)),
                )
            result[index] += addition
            remaining -= addition
    return result


class BoundedTableColumns(QObject):
    """Mantiene columnas interactivas dentro del ancho visible de una tabla."""

    def __init__(
        self,
        table: QTableView,
        *,
        minimum_section_size: int = 48,
        fill_column: int | None = 1,
    ):
        super().__init__(table)
        self._table = table
        self._minimum = max(1, int(minimum_section_size))
        self._captured_minimum = self._minimum
        self._fill_column = fill_column
        self._adjusting = False
        self._scheduled = False
        self._initialization_scheduled = False
        self._initialized = False
        self._preferred_widths: dict[int, int] = {}

        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.installEventFilter(self)
        if table.viewport() is not None:
            table.viewport().installEventFilter(self)
        table.horizontalHeader().sectionResized.connect(self._on_section_resized)
        self.schedule_initialize()

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Show:
            self.schedule_initialize()
        elif event.type() in (QEvent.Resize, QEvent.LayoutRequest):
            if self._initialized:
                self.schedule_fit()
        return super().eventFilter(obj, event)

    def schedule_initialize(self) -> None:
        if self._initialized or self._initialization_scheduled:
            return
        self._initialization_scheduled = True
        QTimer.singleShot(0, self._wait_for_original_layout)

    def _wait_for_original_layout(self) -> None:
        self._initialization_scheduled = False
        try:
            if not self._table.isVisible() or self._table.viewport().width() <= 0:
                return
        except RuntimeError:
            return
        self._initialization_scheduled = True
        QTimer.singleShot(0, self._capture_original_layout)

    def _capture_original_layout(self) -> None:
        self._initialization_scheduled = False
        if self._initialized:
            return
        try:
            table = self._table
            if not table.isVisible():
                return
            header = table.horizontalHeader()
            visible = [
                index
                for index in range(header.count())
                if not header.isSectionHidden(index)
            ]
        except RuntimeError:
            return
        if not visible:
            return
        self._preferred_widths = {
            index: max(1, int(header.sectionSize(index))) for index in visible
        }
        self._captured_minimum = min(
            self._minimum,
            min(self._preferred_widths.values()),
        )
        self._initialized = True
        self.fit_to_viewport()

    def schedule_fit(self) -> None:
        if not self._initialized:
            self.schedule_initialize()
            return
        if self._scheduled:
            return
        self._scheduled = True
        QTimer.singleShot(0, self._run_scheduled_fit)

    def _run_scheduled_fit(self) -> None:
        self._scheduled = False
        self.fit_to_viewport()

    def _on_section_resized(self, logical_index: int, _old_size: int, _new_size: int) -> None:
        if self._adjusting or not self._initialized:
            return
        self.fit_to_viewport(changed_index=int(logical_index))

    def fit_to_viewport(self, *, changed_index: int | None = None) -> None:
        if self._adjusting:
            return
        if not self._initialized:
            self.schedule_initialize()
            return
        try:
            table = self._table
            header = table.horizontalHeader()
            count = int(header.count())
            viewport = table.viewport()
            available = max(0, int(viewport.width()) - 1) if viewport is not None else 0
        except RuntimeError:
            return
        visible = [index for index in range(count) if not header.isSectionHidden(index)]
        if not visible or available <= 0:
            return

        for index in visible:
            self._preferred_widths.setdefault(
                index,
                max(1, int(header.sectionSize(index))),
            )

        effective_minimum = max(
            1,
            min(self._captured_minimum, available // len(visible)),
        )
        self._adjusting = True
        try:
            header.setMinimumSectionSize(effective_minimum)
            header.setStretchLastSection(False)
            for index in visible:
                header.setSectionResizeMode(index, QHeaderView.Interactive)

            local_changed = (
                visible.index(changed_index) if changed_index in visible else None
            )
            local_fill = (
                visible.index(self._fill_column) if self._fill_column in visible else None
            )
            current = (
                [header.sectionSize(index) for index in visible]
                if local_changed is not None
                else [self._preferred_widths[index] for index in visible]
            )
            target = bounded_column_widths(
                current,
                available=available,
                minimum=effective_minimum,
                changed_index=local_changed,
                fill_index=local_fill,
            )
            for logical_index, width in zip(visible, target):
                if header.sectionSize(logical_index) != width:
                    header.resizeSection(logical_index, width)
            if local_changed is not None:
                self._preferred_widths.update(
                    {
                        logical_index: width
                        for logical_index, width in zip(visible, target)
                    }
                )
        finally:
            self._adjusting = False


def install_bounded_columns(
    table: QTableView,
    *,
    minimum_section_size: int = 48,
    fill_column: int | None = 1,
) -> BoundedTableColumns:
    existing = getattr(table, "_bounded_columns_controller", None)
    if isinstance(existing, BoundedTableColumns):
        existing.schedule_fit()
        return existing
    controller = BoundedTableColumns(
        table,
        minimum_section_size=minimum_section_size,
        fill_column=fill_column,
    )
    table._bounded_columns_controller = controller
    return controller


__all__ = [
    "BoundedTableColumns",
    "bounded_column_widths",
    "install_bounded_columns",
]
