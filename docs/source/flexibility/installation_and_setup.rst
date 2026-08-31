.. _flexibility_installation_and_setup:

========================================================
Installation & Simulator Setup (GridLAB-D, OCHRE, pvlib)
========================================================

The Flexibility Modeling framework supports multiple external physical simulation backends. Depending on which asset types your study requires, follow the setup instructions below.

Backend Dependencies Summary
----------------------------

.. list-table::
   :widths: 20 30 50
   :header-rows: 1

   * - Backend
     - Asset Types Supported
     - Setup Difficulty & Requirements
   * - **pvlib**
     - Solar PV arrays, smart inverters, weather modeling
     - **Easy**: Pure Python (``pip install pvlib``)
   * - **OCHRE**
     - Residential dwellings, heat pumps, water heaters, EV, batteries
     - **Easy**: Pure Python (``pip install ochre-nrel``)
   * - **GridLAB-D**
     - Distribution feeders, substations, commercial buildings
     - **Intermediate**: C++ simulation engine + Python bindings
   * - **Replay**
     - Any asset (historical CSV playback)
     - **None**: Built into Python standard library

---

1. Setting Up pvlib
-------------------

``pvlib-python`` provides physical calculations for solar irradiance, panel tilt, clear-sky models, and inverter power output.

**Installation**:

.. code-block:: bash

   pip install pvlib

**Verification**:

.. code-block:: python

   from tent.flexibility import load_adapter

   # 1-line instantiation of a 10 kW solar array in Richland, WA
   pv = load_adapter("residential_pv", rated_kw=10.0)
   print(pv.identity)

---

2. Setting Up OCHRE
-------------------

OCHRE (Object-oriented Controllable High-resolution Residential Energy) models single-family and multi-family homes, HVAC systems, water heaters, and residential batteries.

**Installation**:

.. code-block:: bash

   pip install ochre-nrel

.. note::
   **Python 3.12 / 3.13 Compatibility Note**:
   OCHRE relies on ``Pint`` for unit handling. If you encounter dataclass compatibility issues on modern Python versions, pin ``pint`` to version 0.24.4:

   .. code-block:: bash

      pip install "pint==0.24.4"

**Verification**:

.. code-block:: python

   from tent.flexibility import load_adapter

   # Load a residential dwelling or battery
   battery = load_adapter("residential_battery", capacity_kwh=13.5, max_power_kw=5.0)
   wh = load_adapter("water_heater_50gal")

---

3. Setting Up GridLAB-D
-----------------------

GridLAB-D is PNNL's power distribution simulation engine. It is required when modeling distribution feeder lines, substation transformers, and power flow physics.

Prerequisites:
~~~~~~~~~~~~~~
* **Linux / macOS / Windows Subsystem for Linux (WSL)**
* **GridLAB-D Core Engine**: Installed binary or compiled from source (https://github.com/gridlab-d/gridlab-d).
* **Python C-Bindings**: ``gridlabd`` Python module.

Step-by-Step Setup:
~~~~~~~~~~~~~~~~~~~

1. **Install GridLAB-D C++ Core**:
   On Ubuntu/Debian:

   .. code-block:: bash

      sudo apt-get update
      sudo apt-get install -y gridlabd

   Or build from source following the official GridLAB-D build guide.

2. **Set Environment Variables**:
   Ensure GridLAB-D libraries are discoverable in your environment:

   .. code-block:: bash

      export GLPATH=/usr/local/lib/gridlabd:/usr/local/share/gridlabd
      export PATH=/usr/local/bin:$PATH

3. **Install Python Bindings**:

   .. code-block:: bash

      pip install gridlabd

**Verification**:

.. code-block:: python

   from tent.flexibility import load_adapter

   # Load a GridLAB-D house adapter
   house = load_adapter("gridlabd_house")

---

4. Replay Datasets (No Simulator Required)
------------------------------------------

If you do not have external simulation tools installed, you can use recorded CSV timeseries files using the built-in Replay adapter.

**CSV Format**:

.. code-block:: text

   start,duration_s,q_min_kw,q_max_kw,q_baseline_kw
   2026-08-05T14:00:00+00:00,900,0.0,4.0,2.0
   2026-08-05T14:15:00+00:00,900,0.0,4.0,2.0
   2026-08-05T14:30:00+00:00,900,0.0,4.0,2.0

**Usage**:

.. code-block:: python

   from tent.flexibility.adapters.replay import ReplayAdapter

   adapter = ReplayAdapter.from_csv("path/to/load.csv")

---

Running the Flexibility Test Suite
-----------------------------------

To verify that all adapters and protocols are functioning properly on your system:

.. code-block:: bash

   pytest tests/fmi_tests/ -v
