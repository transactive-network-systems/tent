.. _flexibility_simulator_adapters:

============================
Supported Simulator Adapters
============================

TENT provides direct adapters to industry-standard physical energy modeling and simulation tools.

1. OCHRE Adapter (``tent.flexibility.adapters.ochre``)
------------------------------------------------------

OCHRE (Object-oriented Controllable High-resolution Residential Energy) is NREL's high-resolution residential building simulation library.

* **Capabilities**: Whole-dwelling thermal envelopes, heat pumps, air conditioning, electric resistance water heaters, rooftop PV, residential battery storage, and EV charging.
* **Actuation**: Directly receives thermostat setpoints, battery charging commands, or water heater power targets in Python memory without external IPC overhead.

2. GridLAB-D Adapter (``tent.flexibility.adapters.gridlabd``)
-------------------------------------------------------------

GridLAB-D is PNNL's agent-based power distribution simulation engine.

* **Capabilities**: Multi-zone commercial and residential HVAC systems, water heaters, inverter-coupled battery energy storage, and distribution feeder substations.
* **Actuation**: Sets runtime GridLAB-D object properties (e.g., ``cooling_setpoint``, ``power_out``) dynamically during market iterations.

3. pvlib Adapter (``tent.flexibility.adapters.pvlib``)
-------------------------------------------------------

pvlib is a physics-based tool for simulating the electrical performance of photovoltaic systems.

* **Capabilities**: Solar position calculations, clear-sky irradiance models (Ineichen/Perez), temperature-dependent module efficiency, inverter clipping, and curtailment flexibility.
* **Intermittency**: Includes ``CloudCoverWeather`` for simulating realistic spiky cloud transients.

4. Replay Adapter (``tent.flexibility.adapters.replay``)
--------------------------------------------------------

The Replay adapter reads recorded CSV timeseries files containing columns for ``start``, ``duration_s``, ``q_min_kw``, ``q_max_kw``, and ``q_baseline_kw``.

* **Usage**: Ideal for deterministic regression testing, historical replay studies, and synthetic benchmark validation.
