==================
Configuring a node
==================

A TENT node is configured by one JSON object: the node's own settings, the data manager, and five lists of components (meter_points, information_service_models, local_assets, neighbors, market_series). Every component entry names its class with class_name (and module_name for a class TENT does not ship) and gives that class's settings. The settings, their types, bounds and defaults are the fields of each class's Config model; this page is generated from the JSON Schema those models produce (tent.registry.configuration_schema), so it describes exactly what the installed TENT accepts. Durations are numbers of seconds, times of day are "HH:MM", enumerations are given by member name, and a field marked "in place" may be changed on a running node through reconfigure; any other change rebuilds the node. The configuration tool (tent-config) validates files against the same schema and models.

Node settings
-------------

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the node; neighbors address it by this name.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``actor_id``
     - string or null
     - None
     -  
     - Permanent identity of the actor this node represents, constant across restarts and configuration changes.
     -  
   * - ``organization_id``
     - string or null
     - None
     -  
     - Identity of the business entity the actor belongs to.
     -  
   * - ``tz``
     - string
     - "UTC"
     -  
     - IANA zone name (such as "America/Los_Angeles") the node keeps time in.
     -  
   * - ``mechanism``
     - string
     - "consensus"
     -  
     - Coordination mechanism (future use).
     -  
   * - ``status``
     - string
     - "unknown"
     -  
     - Node status (future use).
     -  
   * - ``loop_sleep_duration``
     - number
     - 0.01
     - >= 0
     - [s] pause between state-machine iterations.
     -  
   * - ``simulation``
     - boolean
     - False
     -  
     - Whether the node runs on simulated time.
     -  
   * - ``simulation_start_time``
     - string or null
     - None
     -  
     - ISO 8601 datetime at which simulated time starts; without an offset it is in tz. Null means the time the node is configured.
     -  
   * - ``simulation_one_hour_in_seconds``
     - integer
     - 3600
     - > 0
     - [s] real seconds per simulated hour.
     -  
   * - ``simulation_anchor_time``
     - string or null
     - None
     -  
     - ISO 8601 real time at which simulated time equals simulation_start_time; shared by every agent of one simulation (the host sets it from its environment). Null means the time this node was created.
     -  
   * - ``curve_archiving``
     - one of "all", "final", "none"
     - "all"
     -  
     - Which curve versions (sd_curve and vertex rows: system curves, signal curves, active vertices) the archive keeps. "all": every version that differs from the last one archived for the same measurement and interval (an unchanged curve is never written twice). "final": only the versions a market archives when it leaves Negotiation and when it clears, the last system curve and the last signal exchanged with each neighbor per interval. "none": no curve rows; measurement and signal headers are still written.
     - yes

Data manager
------------

The archive (class_name and its settings); null means nothing is archived. Registered classes: LocalDataManager, NullDataManager.

LocalDataManager
~~~~~~~~~~~~~~~~

Archives to a SQLAlchemy database (SQLite, PostgreSQL, ...) named by db_params.url.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``archival_buffer_length``
     - integer
     - 10000
     - >= 1
     - Number of queued records above which a warning is logged while the archive is not ready.
     -  
   * - ``db_params``
     - object
     - required
     -  
     - The database connection: url.
     -  

NullDataManager
~~~~~~~~~~~~~~~

A data manager that accepts every record and keeps none. For hosts that do not archive: tests, dry runs, and configuration checks. Table definitions are still parsed into SQLAlchemy tables, so a definition that would fail against a real archive (an unknown column type or enumeration, a redefinition that does not match) fails here as well.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``archival_buffer_length``
     - integer
     - 10000
     - >= 1
     - Number of queued records above which a warning is logged while the archive is not ready.
     -  

Meter points
------------

The meter points. Registered classes: MeterPoint, PushMeterPoint, PullMeterPoint.

MeterPoint
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``data_period_seconds``
     - number
     - 10
     - > 0
     - [s] period between readings; sizes the buffer with max_buffer_seconds and paces a pulled meter.
     -  
   * - ``max_buffer_seconds``
     - number
     - 3600
     - > 0
     - [s] length of reading history kept.
     -  
   * - ``measurement_interval``
     - number [seconds]
     - 3600
     - > 0
     - [s] interval over which readings are averaged (current_interval_measurements).
     -  
   * - ``measurement_type``
     - one of "Voltage", "PowerReal", "PowerReactive", "PriceIncremental", "PriceBlended", "EnergyReal", "EnergyReactive", "PowerMinimum_real", "PowerMaximum_real", "PowerMinimum_reactive", "PowerMaximum_reactive", "ProdVertex", "Temperature", "IsolationDensity", "RelativeHumidity", "ScheduledPower", "EngagementValue", "ReserveMargin", "TransitionCost", "DualCost", "ProductionCost", "ActiveVertex", "AverageDemandkW", "Vertex", "TestVertex", "MarginalPrice", "SystemVertex", "BlendedPrice", "TotalGeneration", "TotalDemand", "NetPower", "EngagementSchedule", "ConvergenceFlag", "PredictedValue", "SignalVertex", "Order", "FullConsensus", "Unknown"
     - "Unknown"
     -  
     - What is measured.
     -  
   * - ``measurement_unit``
     - one of "degF", "kVAR", "kVARh", "kW", "kWh", "Monetary", "Boolean", "Unknown"
     - "Unknown"
     -  
     - Unit of the readings.
     -  
   * - ``scale_factor``
     - number
     - 1.0
     -  
     - [dimensionless] factor applied to every reading; -1 reverses the sign of the meter.
     - yes

PushMeterPoint
~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``data_period_seconds``
     - number
     - 10
     - > 0
     - [s] period between readings; sizes the buffer with max_buffer_seconds and paces a pulled meter.
     -  
   * - ``max_buffer_seconds``
     - number
     - 3600
     - > 0
     - [s] length of reading history kept.
     -  
   * - ``measurement_interval``
     - number [seconds]
     - 3600
     - > 0
     - [s] interval over which readings are averaged (current_interval_measurements).
     -  
   * - ``measurement_type``
     - one of "Voltage", "PowerReal", "PowerReactive", "PriceIncremental", "PriceBlended", "EnergyReal", "EnergyReactive", "PowerMinimum_real", "PowerMaximum_real", "PowerMinimum_reactive", "PowerMaximum_reactive", "ProdVertex", "Temperature", "IsolationDensity", "RelativeHumidity", "ScheduledPower", "EngagementValue", "ReserveMargin", "TransitionCost", "DualCost", "ProductionCost", "ActiveVertex", "AverageDemandkW", "Vertex", "TestVertex", "MarginalPrice", "SystemVertex", "BlendedPrice", "TotalGeneration", "TotalDemand", "NetPower", "EngagementSchedule", "ConvergenceFlag", "PredictedValue", "SignalVertex", "Order", "FullConsensus", "Unknown"
     - "Unknown"
     -  
     - What is measured.
     -  
   * - ``measurement_unit``
     - one of "degF", "kVAR", "kVARh", "kW", "kWh", "Monetary", "Boolean", "Unknown"
     - "Unknown"
     -  
     - Unit of the readings.
     -  
   * - ``scale_factor``
     - number
     - 1.0
     -  
     - [dimensionless] factor applied to every reading; -1 reverses the sign of the meter.
     - yes
   * - ``subscribe_key``
     - string
     - required
     - length >= 1
     - Key the node subscribes to for this meter's readings.
     -  

PullMeterPoint
~~~~~~~~~~~~~~

A meter point that reads its source on a schedule, the counterpart of PushMeterPoint for meters that must be asked. Subclasses implement read_meter(), which talks to the device, service or file and returns one reading. The node's host calls poll() on every meter point once per state-machine iteration; this class's poll() reads when data_period_seconds have passed since the last reading (the first poll reads at once) and stores the reading through set_meter_value, which scales and archives it like a pushed reading. A read_meter() that raises is logged with its traceback and retried on the next period, so one unreachable meter does not stop the node's markets; a read_meter() that returns None is a poll with nothing to record and is also retried on the next period.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``data_period_seconds``
     - number
     - 10
     - > 0
     - [s] period between readings; sizes the buffer with max_buffer_seconds and paces a pulled meter.
     -  
   * - ``max_buffer_seconds``
     - number
     - 3600
     - > 0
     - [s] length of reading history kept.
     -  
   * - ``measurement_interval``
     - number [seconds]
     - 3600
     - > 0
     - [s] interval over which readings are averaged (current_interval_measurements).
     -  
   * - ``measurement_type``
     - one of "Voltage", "PowerReal", "PowerReactive", "PriceIncremental", "PriceBlended", "EnergyReal", "EnergyReactive", "PowerMinimum_real", "PowerMaximum_real", "PowerMinimum_reactive", "PowerMaximum_reactive", "ProdVertex", "Temperature", "IsolationDensity", "RelativeHumidity", "ScheduledPower", "EngagementValue", "ReserveMargin", "TransitionCost", "DualCost", "ProductionCost", "ActiveVertex", "AverageDemandkW", "Vertex", "TestVertex", "MarginalPrice", "SystemVertex", "BlendedPrice", "TotalGeneration", "TotalDemand", "NetPower", "EngagementSchedule", "ConvergenceFlag", "PredictedValue", "SignalVertex", "Order", "FullConsensus", "Unknown"
     - "Unknown"
     -  
     - What is measured.
     -  
   * - ``measurement_unit``
     - one of "degF", "kVAR", "kVARh", "kW", "kWh", "Monetary", "Boolean", "Unknown"
     - "Unknown"
     -  
     - Unit of the readings.
     -  
   * - ``scale_factor``
     - number
     - 1.0
     -  
     - [dimensionless] factor applied to every reading; -1 reverses the sign of the meter.
     - yes

Information service models
--------------------------

The information services (forecasts). Registered classes: TemperatureForecastModel.

TemperatureForecastModel
~~~~~~~~~~~~~~~~~~~~~~~~

Hourly temperature forecast (degF) read from a CSV file (columns Timestamp, Value), the data source used in simulations. The constructor also accepts the keys a service-backed subclass needs (location, max_gap_time, temperature_point_name, tmy_fallback_path) so that such a subclass can fall back to the file when its service fails. A configured file (weather_file_path, or tmy_fallback_path for a service-backed subclass) must exist and parse when the model is configured; a path that does not resolve, a header without Timestamp and Value, or an unparsable row is a configuration error. Relative paths are resolved against the process's working directory and archived resolved.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``information_type``
     - one of "Voltage", "PowerReal", "PowerReactive", "PriceIncremental", "PriceBlended", "EnergyReal", "EnergyReactive", "PowerMinimum_real", "PowerMaximum_real", "PowerMinimum_reactive", "PowerMaximum_reactive", "ProdVertex", "Temperature", "IsolationDensity", "RelativeHumidity", "ScheduledPower", "EngagementValue", "ReserveMargin", "TransitionCost", "DualCost", "ProductionCost", "ActiveVertex", "AverageDemandkW", "Vertex", "TestVertex", "MarginalPrice", "SystemVertex", "BlendedPrice", "TotalGeneration", "TotalDemand", "NetPower", "EngagementSchedule", "ConvergenceFlag", "PredictedValue", "SignalVertex", "Order", "FullConsensus", "Unknown"
     - "Temperature"
     -  
     - Always Temperature for this service (consumers select it by type).
     -  
   * - ``information_units``
     - one of "degF", "kVAR", "kVARh", "kW", "kWh", "Monetary", "Boolean", "Unknown"
     - "degF"
     -  
     - Always degF for this service.
     -  
   * - ``update_period``
     - number [seconds]
     - 3600
     -  
     - [s] period over which the information is reported.
     -  
   * - ``update_interval``
     - number [seconds]
     - 3600
     -  
     - [s] interval between updates of the information.
     -  
   * - ``location``
     - object or null
     - None
     -  
     - Location a service-backed forecast asks about (such as {"lat": 46.2, "long": -119.2}); null for a file-backed forecast.
     - yes
   * - ``max_gap_time``
     - number [seconds]
     - 14400
     - >= 0
     - [s] longest gap in the data a service-backed forecast fills before falling back to the file.
     - yes
   * - ``temperature_point_name``
     - string
     - ""
     -  
     - Name of the temperature point in a service's reply; empty means OutdoorAirTemperature.
     - yes
   * - ``tmy_fallback_path``
     - string
     - ""
     -  
     - CSV file (columns Timestamp, Value in degF) a service-backed forecast falls back to; the forecast file itself when weather_file_path is empty.
     - yes
   * - ``weather_file_path``
     - string or null
     - None
     -  
     - CSV file (columns Timestamp, Value in degF) the forecast is read from; relative paths are resolved against the working directory. Null with an empty tmy_fallback_path means no file.
     - yes

Local assets
------------

The local assets. Registered classes: LocalAsset, OpenLoopLoadPredictor, SimpleBuilding, SolarPvResource, ModelFrameAsset.

LocalAsset
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``default_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] power scheduled when the asset has no better estimate.
     - yes
   * - ``engagement_costs``
     - array
     - [0.0, 0.0, 0.0]
     -  
     - [$] transition costs [disengagement, hold, engagement]; see engagement_cost().
     - yes

OpenLoopLoadPredictor
~~~~~~~~~~~~~~~~~~~~~

Predict electrical load using hour-of-day, season, heating/cooling regime, and forecasted Fahrenheit temperature. # Predictor formula # LOAD = DOW_Intercept(DOW) # + HOUR_SEASON_REGIME_Intercept(HOUR,SEASON,REGIME) # + Factor(HOUR,SEASON,REGIME) * TEMP # DOW_Intercept - average kW - Addend that is a function of categorical # day-of-week. # HOUR - Categorical hour of day in the range [1, 24] # HOUR_SEASON_REGIME_Factor - avg.kW / deg.F - Factor of TEMP. A function # of categoricals HOUR, SEASON, and REGIME. # HOUR_SEASON_REGIME_Intercept - average kW - Addend that is a function # of categoricals HOUR, SEASON, and REGIME. # LOAD - average kW - Predicted hourly Richland, WA electric load # REGIME - Categorical {"Cool", "Heat", or "NA"}. Applies only in seasons # Spring and Fall. Not to be used for Summer or Winter seasons. # SEASON - Categorical season # "Spring" - [Mar, May] # "Summer" - [Jun, Aug] # "Fall" - [Sep, Nov] # "Winter" - [Dec, Feb] # TEMP - degrees Fahrenheit - a predicted hourly temperature forecast.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``default_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] power scheduled when the asset has no better estimate.
     - yes
   * - ``engagement_costs``
     - array
     - [0.0, 0.0, 0.0]
     -  
     - [$] transition costs [disengagement, hold, engagement]; see engagement_cost().
     - yes
   * - ``dow_intercept``
     - array of 7 of number
     - required
     -  
     - [avg.kW] regression intercept per weekday, Monday first.
     - yes
   * - ``season``
     - array of 12 of integer
     - required
     -  
     - Season regime index per month, January first (a row group of values).
     - yes
   * - ``values``
     - array of array
     - required
     -  
     - Regression rows [intercept avg.kW, temperature factor avg.kW/degF], one per hour of the day and season regime.
     - yes
   * - ``default_temperature``
     - number
     - 56.6
     -  
     - [degF] temperature used when no forecast is available.
     - yes
   * - ``scale_factor``
     - number
     - 1.0
     -  
     - [dimensionless] factor applied to the predicted load.
     - yes

SimpleBuilding
~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``default_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] power scheduled when the asset has no better estimate.
     - yes
   * - ``engagement_costs``
     - array
     - [0.0, 0.0, 0.0]
     -  
     - [$] transition costs [disengagement, hold, engagement]; see engagement_cost().
     - yes

SolarPvResource
~~~~~~~~~~~~~~~

Solar PV Resource Model for renewable solar generation. Supports dual-mode execution: 1. Physics-based mode via pvlib / FlexibilityModelInterface when a `site` (pvlib location dict) or `preset` is given. (`location` remains the ordinary Entity text field.) 2. Lightweight cosine approximation (default when no pvlib site is configured).

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``default_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] power scheduled when the asset has no better estimate.
     - yes
   * - ``engagement_costs``
     - array
     - [0.0, 0.0, 0.0]
     -  
     - [$] transition costs [disengagement, hold, engagement]; see engagement_cost().
     - yes
   * - ``cloud_factor``
     - number
     - 1.0
     - >= 0, <= 1
     - [dimensionless] fraction of clear-sky output; 1.0 is clear sky.
     - yes
   * - ``use_pvlib``
     - boolean
     - False
     -  
     - Model the array with pvlib (tent.flexibility) instead of the cosine approximation; implied by site or preset.
     -  
   * - ``site``
     - object or null
     - None
     -  
     - pvlib site: latitude, longitude, tz, altitude.
     -  
   * - ``system``
     - object or null
     - None
     -  
     - pvlib system: surface_tilt, surface_azimuth, ...
     -  
   * - ``weather``
     - object or null
     - None
     -  
     - pvlib weather source; default clear sky scaled by cloud_factor.
     -  
   * - ``preset``
     - string or null
     - None
     -  
     - Name of a tent.flexibility PV preset to build the adapter from.
     -  

ModelFrameAsset
~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``default_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] power scheduled when the asset has no better estimate.
     - yes
   * - ``engagement_costs``
     - array
     - [0.0, 0.0, 0.0]
     -  
     - [$] transition costs [disengagement, hold, engagement]; see engagement_cost().
     - yes
   * - ``model_configs``
     - object or null
     - None
     -  
     - The device models of the frame (demand_curve_points and models). A running asset takes a change that keeps the same models (by subscribe_key and class) and changes only their reconfigurable settings; anything else rebuilds the node.
     - yes
   * - ``temperature_forecast_name``
     - string
     - ""
     - names of information_service_models
     - Name of the temperature forecast service (among information_services) the models use.
     - yes
   * - ``actuation_manager``
     - ActuationManager block
     - None
     -  
     - The actuation manager that writes cleared schedules to the devices; class_name defaults to NullActuationManager. Null means no actuation manager.
     -  
   * - ``occupancy_manager``
     - OccupancyManager block
     - None
     -  
     - The occupancy manager (schedule) that tells the models whether the building is occupied; class_name defaults to SimpleOccupancyManager. Null means always occupied.
     - yes

Neighbors
---------

The neighbors. Registered classes: Neighbor, BulkSupplierDC, ResponseNeighbor.

Neighbor
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``convergence_threshold``
     - number
     - 0.05
     - >= 0
     - [dimensionless fraction] relative change of the exchanged signal below which negotiation with this neighbor counts as converged.
     - yes
   * - ``demand_month``
     - integer or null
     - None
     -  
     - [month number] month in which the demand-charge threshold applies; null means the current (simulated) month at configuration.
     - yes
   * - ``demand_rate``
     - number
     - 4.5
     - >= 0
     - [$/kW per month] demand-charge rate.
     - yes
   * - ``demand_threshold``
     - number
     - 1000000000.0
     - >= 0
     - [kW] power above which demand charges accrue.
     - yes
   * - ``demand_threshold_coef``
     - number
     - 1.0
     - >= 0
     - [dimensionless] factor by which the demand threshold is reduced when a new month starts.
     - yes
   * - ``effective_impedance``
     - number
     - 0.0
     - >= 0
     - [Ohm] effective impedance of the connection (future use).
     - yes
   * - ``friend``
     - boolean
     - False
     -  
     - True for a collaborative neighbor that belongs to the same business entity (offered recovery cost rather than price).
     -  
   * - ``loss_factor``
     - number
     - 0.01
     - >= 0, < 1
     - [dimensionless] full-load loss fraction of the connection; 0.01 is 1%.
     - yes
   * - ``mechanism``
     - string
     - "consensus"
     -  
     - Coordination mechanism (future use).
     -  
   * - ``signal_simplification_tolerance``
     - number
     - 0.0
     - >= 0
     - [fraction of the curve's price and quantity spans] interior vertices of each curve of a prepared transactive signal that lie within this distance of the simplified curve are dropped before the signal is sent and archived (see tent.containers.curve_simplification); 0 sends every vertex. 0.01 is one percent.
     - yes
   * - ``signal_max_vertices``
     - integer or null
     - None
     -  
     - Most vertices a curve of a sent transactive signal may carry; the simplification tolerance is doubled until a curve fits. Null means no cap.
     - yes
   * - ``transactive``
     - boolean
     - False
     -  
     - True when this neighbor exchanges transactive signals; False for a passive supplier or load described by its curve.
     -  
   * - ``up_or_down``
     - one of "bidirectional", "downstream", "unknown", "upstream"
     - "unknown"
     -  
     - Side of this node the neighbor is on: upstream (supply), downstream (load), bidirectional or unknown.
     -  

BulkSupplierDC
~~~~~~~~~~~~~~

A non-transactive bulk electricity supplier (modelled on BPA's relationship to a municipality) whose energy and demand rates depend on the month and on whether an hour is a heavy-load hour (HLH). HLH are 06:00 to 21:59 on every day but Sundays and the configured holidays; `holidays` is a list of ISO dates (the NERC holidays of the simulated years, for example "2018-01-01"). Without it no day is a holiday.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``convergence_threshold``
     - number
     - 0.05
     - >= 0
     - [dimensionless fraction] relative change of the exchanged signal below which negotiation with this neighbor counts as converged.
     - yes
   * - ``demand_month``
     - integer or null
     - None
     -  
     - [month number] month in which the demand-charge threshold applies; null means the current (simulated) month at configuration.
     - yes
   * - ``demand_rate``
     - number
     - 4.5
     - >= 0
     - [$/kW per month] demand-charge rate.
     - yes
   * - ``demand_threshold``
     - number
     - 1000000000.0
     - >= 0
     - [kW] power above which demand charges accrue.
     - yes
   * - ``demand_threshold_coef``
     - number
     - 1.0
     - >= 0
     - [dimensionless] factor by which the demand threshold is reduced when a new month starts.
     - yes
   * - ``effective_impedance``
     - number
     - 0.0
     - >= 0
     - [Ohm] effective impedance of the connection (future use).
     - yes
   * - ``friend``
     - boolean
     - False
     -  
     - True for a collaborative neighbor that belongs to the same business entity (offered recovery cost rather than price).
     -  
   * - ``loss_factor``
     - number
     - 0.01
     - >= 0, < 1
     - [dimensionless] full-load loss fraction of the connection; 0.01 is 1%.
     - yes
   * - ``mechanism``
     - string
     - "consensus"
     -  
     - Coordination mechanism (future use).
     -  
   * - ``signal_simplification_tolerance``
     - number
     - 0.0
     - >= 0
     - [fraction of the curve's price and quantity spans] interior vertices of each curve of a prepared transactive signal that lie within this distance of the simplified curve are dropped before the signal is sent and archived (see tent.containers.curve_simplification); 0 sends every vertex. 0.01 is one percent.
     - yes
   * - ``signal_max_vertices``
     - integer or null
     - None
     -  
     - Most vertices a curve of a sent transactive signal may carry; the simplification tolerance is doubled until a curve fits. Null means no cap.
     - yes
   * - ``transactive``
     - constant False
     - False
     -  
     - A bulk supplier is never transactive.
     -  
   * - ``up_or_down``
     - one of "bidirectional", "downstream", "unknown", "upstream"
     - "unknown"
     -  
     - Side of this node the neighbor is on: upstream (supply), downstream (load), bidirectional or unknown.
     -  
   * - ``holidays``
     - array of string
     - []
     -  
     - ISO dates (YYYY-MM-DD) that are not heavy-load days.
     - yes

ResponseNeighbor
~~~~~~~~~~~~~~~~

A non-transactive neighbor that states only a price or only a quantity (see the module docstring).

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``cost_parameters``
     - array or null
     - None
     -  
     - Coefficients [a0, a1, a2] of the quadratic production-cost formula a0 + a1*p + 0.5*a2*p^2 [$/h with p in kW]; null means no cost.
     - yes
   * - ``default_vertices``
     - array or null
     - None
     -  
     - Vertices of the default supply/demand curve, each [price $/kWh, cost $, quantity avg.kW, continuity, uncertainty] (one to five values, "inf" for an infinite price) or a dict with those keys; null means one inelastic vertex at 1 kW.
     - yes
   * - ``information_services``
     - array or null
     - None
     - names of information_service_models
     - Names of the information services (forecasts) this entity uses; null means none.
     - yes
   * - ``location``
     - string
     - ""
     -  
     - Free-text location.
     - yes
   * - ``maximum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical upper power limit.
     - yes
   * - ``meter_points``
     - array or null
     - None
     - names of meter_points
     - Names of the meter points that measure this entity; null means none.
     - yes
   * - ``minimum_power``
     - number
     - 0.0
     -  
     - [avg.kW, signed, generation positive] physical lower power limit.
     - yes
   * - ``convergence_threshold``
     - number
     - 0.05
     - >= 0
     - [dimensionless fraction] relative change of the exchanged signal below which negotiation with this neighbor counts as converged.
     - yes
   * - ``demand_month``
     - integer or null
     - None
     -  
     - [month number] month in which the demand-charge threshold applies; null means the current (simulated) month at configuration.
     - yes
   * - ``demand_rate``
     - number
     - 4.5
     - >= 0
     - [$/kW per month] demand-charge rate.
     - yes
   * - ``demand_threshold``
     - number
     - 1000000000.0
     - >= 0
     - [kW] power above which demand charges accrue.
     - yes
   * - ``demand_threshold_coef``
     - number
     - 1.0
     - >= 0
     - [dimensionless] factor by which the demand threshold is reduced when a new month starts.
     - yes
   * - ``effective_impedance``
     - number
     - 0.0
     - >= 0
     - [Ohm] effective impedance of the connection (future use).
     - yes
   * - ``friend``
     - boolean
     - False
     -  
     - True for a collaborative neighbor that belongs to the same business entity (offered recovery cost rather than price).
     -  
   * - ``loss_factor``
     - number
     - 0.01
     - >= 0, < 1
     - [dimensionless] full-load loss fraction of the connection; 0.01 is 1%.
     - yes
   * - ``mechanism``
     - string
     - "consensus"
     -  
     - Coordination mechanism (future use).
     -  
   * - ``signal_simplification_tolerance``
     - number
     - 0.0
     - >= 0
     - [fraction of the curve's price and quantity spans] interior vertices of each curve of a prepared transactive signal that lie within this distance of the simplified curve are dropped before the signal is sent and archived (see tent.containers.curve_simplification); 0 sends every vertex. 0.01 is one percent.
     - yes
   * - ``signal_max_vertices``
     - integer or null
     - None
     -  
     - Most vertices a curve of a sent transactive signal may carry; the simplification tolerance is doubled until a curve fits. Null means no cap.
     - yes
   * - ``transactive``
     - boolean
     - False
     -  
     - True when this neighbor exchanges transactive signals; False for a passive supplier or load described by its curve.
     -  
   * - ``up_or_down``
     - one of "bidirectional", "downstream", "unknown", "upstream"
     - "unknown"
     -  
     - Side of this node the neighbor is on: upstream (supply), downstream (load), bidirectional or unknown.
     -  
   * - ``response_type``
     - one of "price", "quantity"
     - required
     -  
     - What the counterparty states: 'price' ($/kWh, a flat curve across the power range) or 'quantity' (kW, an inelastic vertex).
     -  
   * - ``subscribe_key``
     - string
     - required
     - length >= 1
     - Subscription key on which the stated schedule arrives: a list of {"start_time", "value"} entries or a single number for every interval.
     -  
   * - ``curtailment_reward``
     - number or null
     - None
     -  
     - $/kWh paid for curtailing to the stated quantity (quantity form only): a price step at the quantity.
     - yes

Market series
-------------

The market series, each with market_class_name. Registered classes: MarketSeries, MarketSeries, MarketSeries, MarketSeries.

MarketSeries of Market
~~~~~~~~~~~~~~~~~~~~~~

An object of this class contains all the critical timing and initialization properties of the market objects in a series of similar market objects. These properties were initially owned by the market objects and had to be passed from existing to succeeding market objects. Now, many of the properties have been converted to protected properties that are owned jointly by the MarketSeries and Market objects. Eventually, all the properties should be owned by the MarketSeries and not Market objects. This is easy to do because the base Market class now allows a weak reference to its MarketSeries object. Additionally, a MarketSeries constructs its first "dummy" market that jump starts the market state machine for all future markets in the series.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``commitment``
     - boolean
     - False
     -  
     - Whether cleared powers and prices are commitments.
     -  
   * - ``default_price``
     - number
     - 0.05
     - >= 0
     - [$/kWh] price assumed where no market has cleared one.
     - yes
   * - ``delivery_anchor_time``
     - string or null
     - None
     -  
     - Time of day (HH:MM, node zone) at which a delivery period always starts; null means the first market starts at once.
     -  
   * - ``duality_gap_threshold``
     - number
     - 0.01
     - > 0
     - [dimensionless] relative duality gap below which a market counts as converged; 0.01 is 1%.
     - yes
   * - ``duration_active``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] length of the Active period before negotiation.
     -  
   * - ``duration_delivery_lead``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time between clearing and delivery.
     -  
   * - ``duration_market_lead``
     - number [seconds]
     - 3600.0
     - >= 0
     - [s] length of the MarketLead period.
     -  
   * - ``duration_negotiation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allowed for negotiation.
     -  
   * - ``duration_reconciliation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allotted to reconciliation.
     -  
   * - ``future_horizon``
     - number [seconds]
     - 86400
     - > 0
     - [s] forward window of price-discovery relevance (future use).
     -  
   * - ``initial_market_state``
     - one of "Initial", "Active", "Inactive", "Delivery", "DeliveryLead", "Expired", "Exploring", "MarketLead", "Negotiation", "Publish", "Reconcile", "Tender", "Transaction"
     - "Inactive"
     -  
     - State a new market starts in.
     -  
   * - ``interval_duration``
     - number [seconds]
     - 3600
     - > 0
     - [s] length of one delivery interval.
     -  
   * - ``intervals_to_clear``
     - integer
     - 1
     - >= 1
     - Number of intervals each market clears.
     -  
   * - ``market_clearing_interval``
     - number [seconds]
     - 3600.0
     - > 0
     - [s] time between successive market clearings.
     -  
   * - ``market_order``
     - integer
     - 1
     - >= 1
     - Order of this series among the node's series.
     -  
   * - ``method``
     - one of "Interpolation"
     - "Interpolation"
     -  
     - Balancing method.
     -  
   * - ``price_model``
     - PriceModel block
     - None
     -  
     - The price model (class_name, default HourlyAveragePriceModel, and its settings); the series default_price is its default_price unless given here. Null means the default model.
     -  
   * - ``refined_series``
     - string or null
     - None
     - names of market_series
     - Name of the series whose markets this series refines.
     -  
   * - ``actuate_assets``
     - boolean
     - True
     -  
     - Whether a market of this series tells the local assets to actuate when it enters Delivery. False for a series whose delivery periods are refined by another series (a day-ahead market corrected by a real-time one), so that only the refining market actuates.
     -  

MarketSeries of Auction
~~~~~~~~~~~~~~~~~~~~~~~

An object of this class contains all the critical timing and initialization properties of the market objects in a series of similar market objects. These properties were initially owned by the market objects and had to be passed from existing to succeeding market objects. Now, many of the properties have been converted to protected properties that are owned jointly by the MarketSeries and Market objects. Eventually, all the properties should be owned by the MarketSeries and not Market objects. This is easy to do because the base Market class now allows a weak reference to its MarketSeries object. Additionally, a MarketSeries constructs its first "dummy" market that jump starts the market state machine for all future markets in the series.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``commitment``
     - boolean
     - False
     -  
     - Whether cleared powers and prices are commitments.
     -  
   * - ``default_price``
     - number
     - 0.05
     - >= 0
     - [$/kWh] price assumed where no market has cleared one.
     - yes
   * - ``delivery_anchor_time``
     - string or null
     - None
     -  
     - Time of day (HH:MM, node zone) at which a delivery period always starts; null means the first market starts at once.
     -  
   * - ``duality_gap_threshold``
     - number
     - 0.01
     - > 0
     - [dimensionless] relative duality gap below which a market counts as converged; 0.01 is 1%.
     - yes
   * - ``duration_active``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] length of the Active period before negotiation.
     -  
   * - ``duration_delivery_lead``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time between clearing and delivery.
     -  
   * - ``duration_market_lead``
     - number [seconds]
     - 3600.0
     - >= 0
     - [s] length of the MarketLead period.
     -  
   * - ``duration_negotiation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allowed for negotiation.
     -  
   * - ``duration_reconciliation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allotted to reconciliation.
     -  
   * - ``future_horizon``
     - number [seconds]
     - 86400
     - > 0
     - [s] forward window of price-discovery relevance (future use).
     -  
   * - ``initial_market_state``
     - one of "Initial", "Active", "Inactive", "Delivery", "DeliveryLead", "Expired", "Exploring", "MarketLead", "Negotiation", "Publish", "Reconcile", "Tender", "Transaction"
     - "Inactive"
     -  
     - State a new market starts in.
     -  
   * - ``interval_duration``
     - number [seconds]
     - 3600
     - > 0
     - [s] length of one delivery interval.
     -  
   * - ``intervals_to_clear``
     - integer
     - 1
     - >= 1
     - Number of intervals each market clears.
     -  
   * - ``market_clearing_interval``
     - number [seconds]
     - 3600.0
     - > 0
     - [s] time between successive market clearings.
     -  
   * - ``market_order``
     - integer
     - 1
     - >= 1
     - Order of this series among the node's series.
     -  
   * - ``method``
     - one of "Interpolation"
     - "Interpolation"
     -  
     - Balancing method.
     -  
   * - ``price_model``
     - PriceModel block
     - None
     -  
     - The price model (class_name, default HourlyAveragePriceModel, and its settings); the series default_price is its default_price unless given here. Null means the default model.
     -  
   * - ``refined_series``
     - string or null
     - None
     - names of market_series
     - Name of the series whose markets this series refines.
     -  
   * - ``actuate_assets``
     - boolean
     - True
     -  
     - Whether a market of this series tells the local assets to actuate when it enters Delivery. False for a series whose delivery periods are refined by another series (a day-ahead market corrected by a real-time one), so that only the refining market actuates.
     -  

MarketSeries of ConsensusMarket
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An object of this class contains all the critical timing and initialization properties of the market objects in a series of similar market objects. These properties were initially owned by the market objects and had to be passed from existing to succeeding market objects. Now, many of the properties have been converted to protected properties that are owned jointly by the MarketSeries and Market objects. Eventually, all the properties should be owned by the MarketSeries and not Market objects. This is easy to do because the base Market class now allows a weak reference to its MarketSeries object. Additionally, a MarketSeries constructs its first "dummy" market that jump starts the market state machine for all future markets in the series.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``commitment``
     - boolean
     - False
     -  
     - Whether cleared powers and prices are commitments.
     -  
   * - ``default_price``
     - number
     - 0.05
     - >= 0
     - [$/kWh] price assumed where no market has cleared one.
     - yes
   * - ``delivery_anchor_time``
     - string or null
     - None
     -  
     - Time of day (HH:MM, node zone) at which a delivery period always starts; null means the first market starts at once.
     -  
   * - ``duality_gap_threshold``
     - number
     - 0.01
     - > 0
     - [dimensionless] relative duality gap below which a market counts as converged; 0.01 is 1%.
     - yes
   * - ``duration_active``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] length of the Active period before negotiation.
     -  
   * - ``duration_delivery_lead``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time between clearing and delivery.
     -  
   * - ``duration_market_lead``
     - number [seconds]
     - 3600.0
     - >= 0
     - [s] length of the MarketLead period.
     -  
   * - ``duration_negotiation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allowed for negotiation.
     -  
   * - ``duration_reconciliation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allotted to reconciliation.
     -  
   * - ``future_horizon``
     - number [seconds]
     - 86400
     - > 0
     - [s] forward window of price-discovery relevance (future use).
     -  
   * - ``initial_market_state``
     - one of "Initial", "Active", "Inactive", "Delivery", "DeliveryLead", "Expired", "Exploring", "MarketLead", "Negotiation", "Publish", "Reconcile", "Tender", "Transaction"
     - "Inactive"
     -  
     - State a new market starts in.
     -  
   * - ``interval_duration``
     - number [seconds]
     - 3600
     - > 0
     - [s] length of one delivery interval.
     -  
   * - ``intervals_to_clear``
     - integer
     - 1
     - >= 1
     - Number of intervals each market clears.
     -  
   * - ``market_clearing_interval``
     - number [seconds]
     - 3600.0
     - > 0
     - [s] time between successive market clearings.
     -  
   * - ``market_order``
     - integer
     - 1
     - >= 1
     - Order of this series among the node's series.
     -  
   * - ``method``
     - one of "Interpolation"
     - "Interpolation"
     -  
     - Balancing method.
     -  
   * - ``price_model``
     - PriceModel block
     - None
     -  
     - The price model (class_name, default HourlyAveragePriceModel, and its settings); the series default_price is its default_price unless given here. Null means the default model.
     -  
   * - ``refined_series``
     - string or null
     - None
     - names of market_series
     - Name of the series whose markets this series refines.
     -  
   * - ``actuate_assets``
     - boolean
     - True
     -  
     - Whether a market of this series tells the local assets to actuate when it enters Delivery. False for a series whose delivery periods are refined by another series (a day-ahead market corrected by a real-time one), so that only the refining market actuates.
     -  
   * - ``max_balance_iterations``
     - integer
     - 20
     - >= 1
     - Most balancing iterations one local balancing pass takes.
     - yes

MarketSeries of MatchingEngine
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

An object of this class contains all the critical timing and initialization properties of the market objects in a series of similar market objects. These properties were initially owned by the market objects and had to be passed from existing to succeeding market objects. Now, many of the properties have been converted to protected properties that are owned jointly by the MarketSeries and Market objects. Eventually, all the properties should be owned by the MarketSeries and not Market objects. This is easy to do because the base Market class now allows a weak reference to its MarketSeries object. Additionally, a MarketSeries constructs its first "dummy" market that jump starts the market state machine for all future markets in the series.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``name``
     - string
     - ""
     -  
     - Name of the component; unique among the node's components of its kind, and the name other components refer to it by.
     -  
   * - ``description``
     - string
     - ""
     -  
     - Free text.
     -  
   * - ``commitment``
     - boolean
     - False
     -  
     - Whether cleared powers and prices are commitments.
     -  
   * - ``default_price``
     - number
     - 0.05
     - >= 0
     - [$/kWh] price assumed where no market has cleared one.
     - yes
   * - ``delivery_anchor_time``
     - string or null
     - None
     -  
     - Time of day (HH:MM, node zone) at which a delivery period always starts; null means the first market starts at once.
     -  
   * - ``duality_gap_threshold``
     - number
     - 0.01
     - > 0
     - [dimensionless] relative duality gap below which a market counts as converged; 0.01 is 1%.
     - yes
   * - ``duration_active``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] length of the Active period before negotiation.
     -  
   * - ``duration_delivery_lead``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time between clearing and delivery.
     -  
   * - ``duration_market_lead``
     - number [seconds]
     - 3600.0
     - >= 0
     - [s] length of the MarketLead period.
     -  
   * - ``duration_negotiation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allowed for negotiation.
     -  
   * - ``duration_reconciliation``
     - number [seconds]
     - 0.0
     - >= 0
     - [s] time allotted to reconciliation.
     -  
   * - ``future_horizon``
     - number [seconds]
     - 86400
     - > 0
     - [s] forward window of price-discovery relevance (future use).
     -  
   * - ``initial_market_state``
     - one of "Initial", "Active", "Inactive", "Delivery", "DeliveryLead", "Expired", "Exploring", "MarketLead", "Negotiation", "Publish", "Reconcile", "Tender", "Transaction"
     - "Inactive"
     -  
     - State a new market starts in.
     -  
   * - ``interval_duration``
     - number [seconds]
     - 3600
     - > 0
     - [s] length of one delivery interval.
     -  
   * - ``intervals_to_clear``
     - integer
     - 1
     - >= 1
     - Number of intervals each market clears.
     -  
   * - ``market_clearing_interval``
     - number [seconds]
     - 3600.0
     - > 0
     - [s] time between successive market clearings.
     -  
   * - ``market_order``
     - integer
     - 1
     - >= 1
     - Order of this series among the node's series.
     -  
   * - ``method``
     - one of "Interpolation"
     - "Interpolation"
     -  
     - Balancing method.
     -  
   * - ``price_model``
     - PriceModel block
     - None
     -  
     - The price model (class_name, default HourlyAveragePriceModel, and its settings); the series default_price is its default_price unless given here. Null means the default model.
     -  
   * - ``refined_series``
     - string or null
     - None
     - names of market_series
     - Name of the series whose markets this series refines.
     -  
   * - ``actuate_assets``
     - boolean
     - True
     -  
     - Whether a market of this series tells the local assets to actuate when it enters Delivery. False for a series whose delivery periods are refined by another series (a day-ahead market corrected by a real-time one), so that only the refining market actuates.
     -  
   * - ``matching_strategy``
     - array of string
     - ['ORDER_VOLUME']
     -  
     - Tie-break order for same-price orders, by name: NONE, ORDER_AGE, ORDER_VOLUME, PROPORTIONAL_VOLUME. A single name is accepted as a list of one.
     -  
   * - ``order_expiry``
     - number or null
     - None
     -  
     - [s] lifetime of the orders the engine quotes for local entities; null means they stand until the market clears.
     - yes

Price models (market_series[].price_model)
------------------------------------------

A configured PriceModel: one of the registered classes, chosen by class_name. Registered classes: HourlyAveragePriceModel.

HourlyAveragePriceModel
~~~~~~~~~~~~~~~~~~~~~~~

Tracks ths mean and standard deviation of prices for the market series on an hourly basis. Note: In order for this to work, the Market.priceModel table must be initialized from its preceding Market object of the same type, i.e., sharing the same market name.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``default_price``
     - number
     - 0.035
     - >= 0
     - [$/kWh] price assumed before any market has cleared (the series supplies its own default_price).
     -  
   * - ``forgetting_factor``
     - integer
     - 14
     - >= 1
     - Number of prior prices averaged per hour of day.
     -  
   * - ``initial_model``
     - object or null
     - None
     -  
     - A pre-trained model: hour of day (0 to 23) to the forgetting_factor most recent prices [$/kWh]; null starts from default_price.
     -  

Model-frame models (local_assets[].model_configs.models[])
----------------------------------------------------------

A configured Model: one of the registered classes, chosen by class_name. Registered classes: Thermostat, Lighting, UncontrolledLoad, FlexibilityModelFrameAdapter.

Thermostat
~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``subscribe_key``
     - string
     - required
     - length >= 1
     - Key the node subscribes to for this model's measurements; unique within the frame.
     -  
   * - ``model_config``
     - object
     - required
     -  
     -  
     -  

Lighting
~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``subscribe_key``
     - string
     - required
     - length >= 1
     - Key the node subscribes to for this model's measurements; unique within the frame.
     -  
   * - ``model_config``
     - object
     - required
     -  
     -  
     -  

UncontrolledLoad
~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``subscribe_key``
     - string
     - required
     - length >= 1
     - Key the node subscribes to for this model's measurements; unique within the frame.
     -  
   * - ``model_config``
     - object
     - required
     -  
     -  
     -  

FlexibilityModelFrameAdapter
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Wraps any FlexibilityModelInterface adapter as a Model in TENT's ModelFrame. Allows OCHRE dwellings, GridLAB-D houses/water heaters/feeders, pvlib solar arrays, and replay datasets to be nested directly in a ModelFrameAsset configuration. Sign Convention: FMI adapters are canonical load-positive (consumption > 0, generation < 0). TENT ModelFrame is generation-positive (consumption < 0, generation > 0). This adapter transparently negates power values to maintain consistency with TENT.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``subscribe_key``
     - string
     - required
     - length >= 1
     - Key the node subscribes to for this model's measurements; unique within the frame.
     -  
   * - ``model_config``
     - object
     -  
     -  
     -  
     -  

Actuation managers (local_assets[].actuation_manager)
-----------------------------------------------------

A configured ActuationManager: one of the registered classes, chosen by class_name. Registered classes: DirectRatioActuationManager, ExternalTargetActuationManager, NullActuationManager.

DirectRatioActuationManager
~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``active_onstart``
     - boolean
     - True
     -  
     - Whether actuation is allowed as soon as the node starts.
     -  
   * - ``control_interval``
     - number [seconds]
     - 60
     - >= 0
     - [s] interval between control actions.
     -  

ExternalTargetActuationManager
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``active_onstart``
     - boolean
     - True
     -  
     - Whether actuation is allowed as soon as the node starts.
     -  
   * - ``control_interval``
     - number [seconds]
     - 60
     - >= 0
     - [s] interval between control actions.
     -  

NullActuationManager
~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``active_onstart``
     - boolean
     - True
     -  
     - Whether actuation is allowed as soon as the node starts.
     -  
   * - ``control_interval``
     - number [seconds]
     - 60
     - >= 0
     - [s] interval between control actions.
     -  

Occupancy managers (local_assets[].occupancy_manager)
-----------------------------------------------------

A configured OccupancyManager: one of the registered classes, chosen by class_name. Registered classes: SimpleOccupancyManager.

SimpleOccupancyManager
~~~~~~~~~~~~~~~~~~~~~~

Occupancy from a weekly schedule: for each weekday either "always_on", "always_off", or {"start": "HH:MM", "end": "HH:MM"} (occupied from start up to end). An empty schedule means always occupied; a schedule that names some days but not all is a configuration error, since the missing days would have no answer.

.. list-table::
   :header-rows: 1
   :widths: 18 16 12 14 32 8

   * - Field
     - Type
     - Default
     - Bounds
     - Description
     - In place
   * - ``schedule``
     - object
     - {}
     -  
     - Weekday (monday ... sunday) to "always_on", "always_off" or {"start": "HH:MM", "end": "HH:MM"} (occupied from start up to end). Empty means always occupied; otherwise every weekday is given.
     - yes
