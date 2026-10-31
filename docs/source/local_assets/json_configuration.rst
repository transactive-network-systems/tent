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
     "module_name": "tent.local_asset.model_frame.model_frame_asset",
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
           "model_type": "fmi",
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
           "model_type": "uncontrolled",
           "model_config": {
             "default_power": -45.0
           }
         }
       ]
     }
   }

Configuration Properties
------------------------

* ``class_name``: Must be ``"ModelFrameAsset"``.
* ``module_name``: Must be ``"tent.local_asset.model_frame.model_frame_asset"``.
* ``maximum_power``: Upper generation or curtailment boundary in kW.
* ``minimum_power``: Maximum allowable consumption in kW (negative in TENT convention).
* ``model_configs.models``: Array of nested sub-model definitions. For physical models, set ``"model_type": "fmi"``.
