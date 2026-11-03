from collections.abc import Callable, Iterable

from pydm.widgets.channel import PyDMChannel
from qtpy.QtCore import QObject, QTimer, Signal, Slot


class PVUnavailableError(RuntimeError):
    """A required PV has not supplied a connected value."""


class PVSubscription(QObject):
    """Receive values and connection state through queued PyDM callbacks."""

    changed = Signal()

    def __init__(self, pv: str, parent: QObject | None):
        super().__init__(parent)
        self.connected = False
        self.value = None
        self.enums = ()
        self.channel = PyDMChannel(
            address=f"ca://{pv}",
            connection_slot=self.connection_changed,
            value_slot=self.value_changed,
            enum_strings_slot=self.enums_changed,
        )

    @Slot(bool)
    def connection_changed(self, connected: bool):
        self.connected = connected
        if not connected:
            self.value = None
            self.enums = ()
        self.changed.emit()

    @Slot(int)
    @Slot(float)
    @Slot(str)
    @Slot(bool)
    @Slot(object)
    def value_changed(self, value):
        if not self.connected:
            return
        self.value = value
        self.changed.emit()

    @Slot(tuple)
    def enums_changed(self, enums: tuple):
        self.enums = enums
        self.changed.emit()

    def read(self, as_string: bool = False):
        if not self.connected or self.value is None:
            raise PVUnavailableError("PV is disconnected or has no value")
        if as_string:
            if isinstance(self.value, int) and self.enums:
                if not 0 <= self.value < len(self.enums):
                    raise PVUnavailableError("PV enum value is outside its enum labels")
                return self.enums[self.value]
            return str(self.value)
        return self.value

    def close(self):
        self.channel.disconnect()
        self.deleteLater()


class PVReader(QObject):
    """Collect asynchronous channel values with a bounded, cancellable wait."""

    def __init__(self, parent: QObject | None):
        super().__init__(parent)
        self.subscriptions: dict[str, PVSubscription] = {}
        self.pending: tuple[str, ...] = ()
        self.callback: Callable[[dict], None] | None = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.complete)

    def read(self, names: Iterable[str], callback: Callable[[dict], None]):
        if self.callback is not None:
            raise RuntimeError("A PV read is already pending")
        self.pending = tuple(dict.fromkeys(names))
        self.callback = callback
        for name in self.pending:
            if name not in self.subscriptions:
                subscription = PVSubscription(name, self)
                subscription.changed.connect(self.check_ready)
                self.subscriptions[name] = subscription
                subscription.channel.connect()
        self.timer.start(10_000)
        QTimer.singleShot(0, self.check_ready)

    @Slot()
    def check_ready(self):
        if self.callback is None:
            return
        if all(
            self.subscriptions[name].connected
            and self.subscriptions[name].value is not None
            for name in self.pending
        ):
            self.complete()

    @Slot()
    def complete(self):
        if self.callback is None:
            return
        self.timer.stop()
        values = {
            name: subscription.value
            for name in self.pending
            if (subscription := self.subscriptions[name]).connected
            and subscription.value is not None
        }
        callback = self.callback
        self.callback = None
        self.pending = ()
        callback(values)

    def value(self, name: str, as_string: bool = False):
        if name not in self.subscriptions:
            raise PVUnavailableError(f"No subscription for {name}")
        try:
            return self.subscriptions[name].read(as_string)
        except PVUnavailableError as exc:
            raise PVUnavailableError(f"{name}: {exc}") from exc

    def reset(self):
        self.timer.stop()
        self.callback = None
        self.pending = ()
        for subscription in self.subscriptions.values():
            subscription.close()
        self.subscriptions.clear()
