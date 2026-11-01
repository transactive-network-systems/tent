.. _Build_Configurations:

==================================
Building Component Configurations
==================================

TENT configurations are organized as declarative JSON files that specify node parameters, assets, neighbors, and market timing.

Configuration Structure
-----------------------

A node configuration is one JSON object whose keys are the fields of ``TransactiveNode.Config`` (see
:ref:`Configuration_Models` for how every block's keys are read from its model):

1. **Node settings**: ``name``, ``description``, ``actor_id`` (the permanent identity of the actor this node
   represents), ``organization_id``, ``tz`` (an IANA zone name), ``mechanism``, ``status``, ``loop_sleep_duration``.

2. **Simulation settings**: ``simulation``, ``simulation_start_time`` (ISO 8601; without an offset it is in ``tz``),
   ``simulation_one_hour_in_seconds``, ``simulation_anchor_time`` (set by the host from its environment).

3. **Host settings** read by the TransactiveNodeAgent, not by the node: ``db_topic``, ``subscribe_all_platforms``,
   ``topics``.

4. **The archive** (``data_manager``): one block naming the data manager class and its settings
   (``LocalDataManager`` with ``db_params.url``, or ``NullDataManager``); without it nothing is archived.

5. **The component lists**, built in this order so that an entry may refer by name to one before it:
   ``meter_points``, ``information_service_models``, ``local_assets``, ``neighbors``, ``market_series``. Each entry is
   a block naming its class and giving that class's settings; a ``market_series`` entry also names its market class
   with ``market_class_name`` and holds that class's settings.

Any other key is an error, as is a key a block's class does not declare. ``sample_configs/tn.campus.json`` is a
complete example.

Naming Classes
--------------

Every configurable block names its class with ``class_name``; the block's other keys are passed to the class as
constructor keywords. ``tent.registry`` resolves the name in this order:

1. ``module_name`` given in the block: the module is imported and the class taken from it. This is the path for
   classes TENT does not ship, and it works for shipped classes too.
2. Otherwise the name is looked up among the classes TENT ships, registered by name in ``tent.registry.SHIPPED``.
3. Otherwise among the ``tent.components`` entry points of installed packages. A package registers
   ``MyAsset = "my_package.assets:MyAsset"`` under that group and a configuration can name ``MyAsset`` alone.

No module path is derived from a class name. The resolved class must be a subclass of the base its slot expects
(``LocalAsset``, ``Neighbor``, ``MeterPoint``, ``InformationServiceModel``, ``MarketSeries``, ``Market``,
``PriceModel``, ``DataManager``, the model-frame ``Model``, ``OccupancyManager``, ``ActuationManager``); otherwise
configuration fails with a ``TypeError`` naming the slot. An unknown name without ``module_name`` fails with a
``ValueError`` listing the shipped and registered names. A market series names its market class with
``market_class_name`` (and ``market_module_name`` for one from elsewhere); without it the series spawns plain
``Market`` objects. Configuration dictionaries are only read, never changed, so the same contents can configure a node
again.

Validation
----------

``TransactiveNode.validate_configuration(configs)`` checks a whole configuration through the models without building
anything, and ``configure_node`` does so before it builds; the error names the list entry, the component and the keys.
``tent.registry.configuration_schema()`` returns the JSON Schema of a node configuration for editors and tools. Both are
described in :ref:`Configuration_Models`.
