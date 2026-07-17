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
