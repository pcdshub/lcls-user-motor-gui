from qtpy.QtCore import Signal
from qtpy.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QCompleter,
    QDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)


class FilteredListWidget(QWidget):
    currentIndexChanged = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.line_edit = QLineEdit(self)
        self.line_edit.setPlaceholderText("Type to filter...")
        layout.addWidget(self.line_edit)
        self.list_widget = QListWidget(self)
        layout.addWidget(self.list_widget)
        self._all_items = []

        # Connections
        self.line_edit.textChanged.connect(self.filter_items)
        self.list_widget.currentRowChanged.connect(self.currentIndexChanged)

    def add_items(self, items):
        self._all_items.extend(items)
        self.filter_items(self.line_edit.text())

    def add_item(self, item, *, allow_duplicates=True):
        """Add a single item and update the visible list respecting the filter."""
        if not allow_duplicates and item in self._all_items:
            return False

        self._all_items.append(item)

        # Only add to the visible list if it matches the current filter
        text = self.line_edit.text()
        if text.lower() in item.lower():
            self.list_widget.addItem(item)
        return True

    def filter_items(self, text):
        self.list_widget.clear()
        filtered = [item for item in self._all_items if text.lower() in item.lower()]
        self.list_widget.addItems(filtered)
        # if filtered:
        #     self.list_widget.setCurrentRow(0)
        # else:
        #     self.list_widget.setCurrentRow(-1)

    def currentRow(self):
        return self.list_widget.currentRow()

    def currentText(self):
        item = self.list_widget.currentItem()
        return item.text() if item else None

    def all_items(self):
        """Return all items, including any hidden by the current filter."""
        return list(self._all_items)

    def clear_items(self):
        """Remove all items from both the widget and the source list."""
        self._all_items.clear()
        self.list_widget.clear()
