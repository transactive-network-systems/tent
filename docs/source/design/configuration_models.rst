.. _Configuration_Models:

====================
Configuration Models
====================

Every configurable class in TENT declares a nested ``Config`` model (Pydantic v2, built on ``tent.config.ConfigModel``)
that is the contract for its block of the node configuration. The constructor validates its keyword arguments through
that model and reads its settings from the validated instance, so the keys a block accepts, their types, bounds,
defaults and descriptions are the model's fields and nothing else. There is no second list to keep in step: this page
describes how to read the models, not the keys themselves.

The classes with a ``Config`` are the data managers (``DataManager``, ``LocalDataManager``, ``NullDataManager``), the
market series and the markets (``MarketSeries``, ``Market``, ``Auction``, ``ConsensusMarket``, ``MatchingEngine``), the
price models (``HourlyAveragePriceModel``), the neighbors (``Neighbor``, ``BulkSupplierDC``), the local assets
(``LocalAsset``, ``OpenLoopLoadPredictor``, ``SimpleBuilding``, ``SolarPvResource``, ``ModelFrameAsset``) and the
model-frame models (``Thermostat``, ``Lighting``, ``UncontrolledLoad``, ``FlexibilityModelFrameAdapter``), the actuation
and occupancy managers, the information services (``InformationServiceModel``, ``TemperatureForecastModel``), the meter
points (``MeterPoint``, ``PushMeterPoint``, ``PullMeterPoint``) and the ``TransactiveNode`` itself. A subclass's model
extends its parent's, so ``BulkSupplierDC.Config`` has every ``Neighbor`` key plus ``holidays``.

Reading the keys of a class
---------------------------

The fields of a model are its configuration keys. ``model_fields`` gives each field's type, default and description
(units are stated in the description, in brackets); ``model_json_schema()`` gives the same as JSON Schema::

   from tent.neighbor import Neighbor

   for name, field in Neighbor.Config.model_fields.items():
       print(name, field.annotation, field.default, field.description)

   Neighbor.Config.model_json_schema()        # the block's JSON Schema
   Neighbor.Config.reconfigurable_fields()    # the fields a running neighbor may change in place

A market series block holds two models' keys: ``MarketSeries.Config`` and the ``Config`` of the class named by
``market_class_name`` (``Market.Config`` has ``actuate_assets``, true by default and false for a series whose
deliveries another series refines, so that only the refining market actuates on entering Delivery;
``ConsensusMarket.Config`` adds ``max_balance_iterations``, ``MatchingEngine.Config`` adds ``matching_strategy`` and
``order_expiry``). A model-frame
model's block (``model_configs.models[]``) has ``class_name``, ``module_name``, ``subscribe_key`` and ``model_config``;
the keys of ``model_config`` are the model class's ``Config`` fields other than ``subscribe_key`` and
``demand_curve_points``, which the frame supplies.

Value conventions
-----------------

The models encode TENT's conventions as types (``tent.config``):

===========================  ==================================================================================
Convention                   In the configuration
===========================  ==================================================================================
Durations                    A number of seconds (``"duration_market_lead": 600``); read as ``timedelta``.
                             Fields that must be positive or non-negative say so in their schema and messages.
Times of day                 ``"HH:MM"`` (``"delivery_anchor_time": "02:00"``); read as ``time``.
Enumerations                 The member's name (``"up_or_down": "upstream"``, ``"measurement_type": "PowerReal"``,
                             ``"initial_market_state": "Inactive"``). Values (integers) are not accepted.
``cost_parameters``          Exactly three numbers ``[a0, a1, a2]``, or ``null`` for no cost.
``default_vertices``         A list of vertices, each ``[price, cost, quantity, continuity, uncertainty]`` (one to
                             five values; an infinite price is ``"inf"``) or a dict with those keys; ``null`` for
                             one inelastic vertex.
Datetimes                    ISO 8601 with an offset. ``simulation_start_time`` and ``simulation_anchor_time``
                             without an offset are in the node's ``tz``; every other naive datetime is an error.
Name references              ``meter_points``, ``information_services``, ``temperature_forecast_name`` and
                             ``refined_series`` name other components; a name that matches nothing is an error.
Nested objects               A dict with ``class_name`` (``module_name`` for a class TENT does not ship) and the
                             named class's settings: ``price_model``, ``occupancy_manager``, ``actuation_manager``,
                             ``model_configs.models[]``, ``data_manager``. ``price_model`` without ``class_name``
                             is an ``HourlyAveragePriceModel``; the managers default to ``SimpleOccupancyManager``
                             and ``NullActuationManager``.
``null``                     Where a field allows it, ``null`` means the default (no cost parameters, no meter
                             points, no price model block).
===========================  ==================================================================================

Unknown keys are errors. ``TransactiveNode.validate_configuration(configs)`` validates a whole configuration without
building anything: the node's keys, then every component's block through its class's ``Config`` and, recursively, the
nested blocks. ``configure_node`` calls it first, so a configuration the models reject fails before any component
exists, with a ``tent.config.ConfigurationError`` (a ``ValueError``) whose message names the list entry, the component
class and name, and every offending key::

   neighbors[0]: Neighbor 'CoR': unknown configuration key 'loss_factr' (the keys of Neighbor.Config are [...])
   market_series[0]: MarketSeries 'Core' (market settings of Auction): unknown configuration key 'actuate_asets' (...)
   local_assets[1]: ModelFrameAsset 'Office'.model_configs.models[0]: Lighting 'lights': rated_power is required

The camelCase list names of the earlier agent format (``meterPoints``, ``localAssets``, ``markets``, ...) are errors
that name the current key. A class from another package that extends a shipped class without declaring its own
``Config`` (``Config`` is inherited, not declared) is not pre-validated; its constructor consumes its own keywords and
the shipped base validates the rest.

Reconfigurable fields
---------------------

A field a running component may change in place is declared with ``Setting(..., reconfigurable=True)``, which puts
``"reconfigurable": true`` in the field's JSON Schema; ``Config.reconfigurable_fields()`` lists them. They are the
costs, limits, thresholds, default prices, loss factors, demand rates, forecast sources and schedules. Class and module
names, market timing, topics and subscription keys, data manager settings and the node's own settings (including the
simulation block) are not reconfigurable: changing them rebuilds the node.

``component.reconfigure(changes)`` validates the changed fields against the model (the whole configuration is
re-validated, so cross-field rules hold), applies them, and archives the new configuration version. A change that
names a field that is not reconfigurable, or a key that is not a field, raises ``tent.config.ReconfigurationError``
listing the refused fields, and nothing is applied. A market series takes changes to its own reconfigurable fields and
to its market class's (``max_balance_iterations``, ``order_expiry``), applying the latter to the live markets and to
every market spawned later. A model-frame asset takes a new ``model_configs`` block when it keeps the same models (by
``subscribe_key`` and class) and the same ``demand_curve_points``, applying each model's changed reconfigurable
settings (``default_lighting_schedule``, ``uncontrolled_load_schedule``, the thermostat coefficients and set points)
to the model object in place; a new ``occupancy_manager`` block rebuilds the occupancy manager.

``TransactiveNode.reconfigure(new_config)`` takes a whole new configuration and compares it, field by field through
the models, with the configuration the node was built from. When the two differ only in reconfigurable fields of
existing components (matched by list, name and class, and for a market series also by market class) it applies the
changes through each component's ``reconfigure`` and returns ``True``; otherwise it changes nothing, logs the reason and
returns ``False`` so that the host rebuilds the node. A new configuration the models reject raises as ``configure_node``
would. The node keeps the configuration it was built from as ``configuration`` (the dict as given) and ``config``
(validated).

Configuration versions in the archive
-------------------------------------

Every component archives its configuration in the append-only ``component_configuration`` table: one row at
construction (version 1) and one per in-place reconfiguration, with the component id, the version, the record time,
the class name and the validated configuration as JSON (durations in seconds, enumerations by name, ``"inf"`` for an
infinite price). The component's own row is written once; its history of settings is this table.

JSON Schema of a node configuration
-----------------------------------

``tent.registry.configuration_schema()`` assembles the JSON Schema (draft 2020-12) of a whole node configuration from
the models: the node's own keys, and for the data manager, every component list and every nested block a ``oneOf``
over the registered classes of that slot (the classes TENT ships and those installed packages register under the
``tent.components`` entry-point group), each variant discriminated by its ``class_name`` constant and allowing no
undeclared keys. A market series has one variant per (series class, market class) pair, discriminated by
``class_name`` and ``market_class_name``. The reconfigurable marker, the units of duration fields
(``x-tent-unit``), the slot of a nested block (``x-tent-spec``) and the list a name reference points into
(``x-tent-reference``) are in the schema for editors and the configuration tool::

   import json
   from tent import registry

   with open('tent-node.schema.json', 'w') as f:
       json.dump(registry.configuration_schema(), f, indent=2)

The sample configuration ``sample_configs/tn.campus.json`` validates against this schema, and the test suite checks
that every registered class appears in it.
