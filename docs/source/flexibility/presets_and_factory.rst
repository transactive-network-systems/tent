.. _flexibility_presets_and_factory:

=================================
Presets and Configuration Factory
=================================

TENT provides a convenient preset factory (``load_adapter`` and ``from_config``) under ``tent.flexibility`` to instantiate physical adapters with minimal boilerplate.

Built-in Presets
----------------

The following preset identifiers are available out of the box:

* ``"residential_pv"``: 5 kW rooftop solar PV system.
* ``"commercial_pv"``: 100 kW commercial building PV array.
* ``"residential_battery"``: 13.5 kWh / 5 kW residential battery storage system.
* ``"commercial_battery"``: 100 kWh / 50 kW commercial battery energy storage.
* ``"water_heater_50gal"``: 50-gallon, 4.5 kW residential electric water heater.
* ``"water_heater_80gal"``: 80-gallon, 4.5 kW residential electric water heater.
* ``"dwelling_standard"``: Standard single-family residential dwelling modeled via OCHRE.
* ``"gridlabd_house"``: Standard residential house modeled via GridLAB-D.
* ``"gridlabd_feeder"``: IEEE prototypical distribution feeder substation.

Python Usage
------------

.. code-block:: python

   from tent.flexibility import load_adapter

   # Load a preset with custom capacity override
   pv = load_adapter("residential_pv", rated_kw=10.0)

   # Load a water heater
   wh = load_adapter("water_heater_50gal")

   # Load battery storage with custom efficiency
   battery = load_adapter(
       "residential_battery",
       capacity_kwh=20.0,
       max_power_kw=7.0,
       round_trip_efficiency=0.92,
   )

JSON Dictionary Usage
---------------------

In JSON configuration files (such as ``tn.campus.json``), presets can be loaded directly:

.. code-block:: json

   {
     "preset": "residential_pv",
     "resource_id": "campus_pv",
     "rated_kw": 120.0,
     "location": {
       "latitude": 46.28,
       "longitude": -119.28,
       "tz": "US/Pacific"
     }
   }
