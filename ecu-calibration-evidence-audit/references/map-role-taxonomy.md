# ECU MAP role taxonomy

Use control direction, inputs, output units, code consumers, and enable conditions together. Do not classify by label or surface shape alone.

| Role | Typical direction | Typical consumer | Common confusion | Edit rule |
|---|---|---|---|---|
| Sensor transfer | voltage/ADC/frequency -> physical signal | plausibility, filtering, sensor model | mistaken for throttle/pedal response | change only for verified hardware transfer changes |
| Driver Demand | normalized pedal + rpm/mode -> request | torque/load arbitration | confused with throttle angle or inverse torque | verify active page and low-speed/gear behavior |
| Request shaping | existing request + state -> reshaped request | rate limiter, reserve, arbitration | confused with primary Driver Demand | identify upstream request and selection state |
| Torque/load limiter | request + condition -> allowed maximum/minimum | min/max arbitration | confused with target table | change only when LOG identifies it as active constraint |
| Inverse torque model | target torque + rpm/condition -> air/load | throttle/boost/air-path target | confused with Driver Demand because one axis is Nm | keep consistent with forward model and air model |
| Forward torque model | air/load + rpm/condition -> estimated torque | monitor, TCU report, arbitration | confused with requested torque | do not inflate to manufacture response |
| Actuator/plant model | desired flow/pressure + state -> actuator command | throttle, VVT, WG, pump, pressure control | confused with demand because output rises with load | change only with measured model error |
| Monitor/plausibility model | command/model/actual -> residual or allowed band | diagnostic or torque monitor | mistaken for duplicate performance table | preserve unless monitor calibration is independently justified |
| Protection | temperature/pressure/speed/time -> limit or intervention | thermal, knock, component protection | called an OEM “restriction” | retain; require component specs and measured validation |
| ECU-TCU interface | load/torque/gear/state -> reported or allowed torque | shift, clutch, TCC, line pressure | mistaken for engine power target | verify torque handshake and transmission capacity |
| Correction/compensation | base quantity + temperature/voltage/environment -> correction | final command | mistaken for base table | leave stock until base model is validated |

## Direction tests

Ask these questions in order:

1. Is the upstream value a measured signal, a driver request, an arbitrated request, or a modeled state?
2. Does the lookup output a request, a limit, an air target, an actuator command, an estimated torque, or an error band?
3. Is the result selected with `min`, `max`, state banks, hysteresis, or enable flags?
4. Is the result reported to the TCU/diagnostics or used to drive an actuator?
5. Does a sibling or inverse table mathematically contradict the proposed name?

## Evidence language

- `OBSERVED`: visible in file, definition, screenshot, or LOG.
- `VERIFIED`: independently cross-confirmed by definition/code/physical behavior.
- `INFERRED`: strong engineering fit with a missing link.
- `UNKNOWN`: insufficient or contradictory evidence.

Use semantic grades separately: A, B+, B, C. A readable decompiler result does not automatically produce an A-grade OEM name.
