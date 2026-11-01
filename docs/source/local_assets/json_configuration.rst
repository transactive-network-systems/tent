.. _local_assets_json_configuration:

=====================================
JSON Asset Configuration Guide
=====================================

Transactive nodes and assets in TENT are fully configurable via JSON files.

Defining a ModelFrameAsset in JSON
----------------------------------

Below is an annotated JSON structure defining a campus building asset with physical solar PV and building loads:

.. code-block:: json

   {
     "class_name": "ModelFrameAsset",
     "name": "CampusEngineeringBuilding",
     "description": "Multi-resource campus facility with rooftop PV and flexible HVAC",
     "default_power": 0.0,
     "default_vertices": [[0, 0, 30.0, true]],
     "maximum_power": 250.0,
     "minimum_power": -500.0,
     "temperature_forecast_name": "OutdoorTemperatureForecast",
     "model_configs": {
       "demand_curve_points": 2,
       "models": [
         {
           "subscribe_key": "devices/rooftop_solar",
           "class_name": "FlexibilityModelFrameAdapter",
           "model_config": {
             "preset": "residential_pv",
             "rated_kw": 120.0,
             "location": {
               "latitude": 46.28,
               "longitude": -119.28,
               "tz": "US/Pacific"
             },
             "system": {
               "surface_tilt": 30,
               "surface_azimuth": 180
             },
             "weather": {
               "kind": "clearsky",
               "cloud_factor": 1.0
             },
             "curtailable": false
           }
         },
         {
           "subscribe_key": "devices/uncontrolled_baseload",
           "class_name": "UncontrolledLoad",
           "model_config": {
             "uncontrolled_load_schedule": [45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0,
                                            45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0, 45.0]
           }
         }
       ]
     }
   }

Configuration Properties
------------------------

* ``class_name``: The asset class, here ``"ModelFrameAsset"``. Classes TENT ships are named alone; a class from
  another package also gives ``module_name`` (see :ref:`Build_Configurations`).
* ``maximum_power``: Upper generation or curtailment boundary in kW.
* ``minimum_power``: Maximum allowable consumption in kW (negative in TENT convention).
* ``model_configs.models``: Array of nested sub-model definitions. Each names its class with ``class_name``
  (``Thermostat``, ``Lighting``, ``UncontrolledLoad``, or ``FlexibilityModelFrameAdapter`` for a physical simulator
  model; a class from another package also gives ``module_name``), the topic it listens to with ``subscribe_key``, and
  the model's own settings in ``model_config``. Every model class is a subclass of
  ``tent.local_asset.model_frame.models.Model``.
* ``occupancy_manager`` and ``actuation_manager``: Optional blocks naming their class the same way;
  ``SimpleOccupancyManager`` and ``NullActuationManager`` apply when a block gives no ``class_name``.
* Every key of the asset block and of each ``model_config`` is a field of the class's ``Config`` model
  (``ModelFrameAsset.Config``, ``Lighting.Config``, ...); an unknown key is a configuration error naming the asset,
  the model and the key. The fields, their units and which of them a running asset may change in place are read from
  the models as described in :ref:`Configuration_Models`.
