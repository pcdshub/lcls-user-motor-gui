# lcls-user-motor-gui

The lcls-user-motor-gui application is used to configure the user motors Beckhoff control box.

See the [confluence docs](https://confluence.slac.stanford.edu/x/KQnRJ) for more information.

## Try in dev

To run in dev with the test PLC:

```
pixi run gui
```


This assumes you're using the test PLC, the expanded command is:

```
pixi run lcls-user-motor-gui -l INFO gui --ioc-name ioc-lcls-plc-template-user-motors
```

## Stage configuration reads

Stage settings reads selector, access, and snapshot PVs through asynchronous
PyDM channel callbacks. Each read phase waits up to 10 seconds and offers Cancel;
closing the dialog releases its subscriptions. NC-only saved configs do not
require COE selector PVs.

Unavailable selector or access data stops the operation rather than guessing
hardware or treating unknown access as writable. A connected selector reporting
`None` indicates no linked hardware. Changes to the IOC, axis/config selection,
or prerequisite values invalidate the pending operation.

If some snapshot readbacks are unavailable, saving only the readable values
requires confirmation (Cancel is the default). No file is written if all reads
fail. Applying saved values still uses the existing synchronous write path.
