.. _Build_Configurations:

==================================
Building Component Configurations
==================================

TENT configurations are organized as declarative JSON files that specify node parameters, assets, neighbors, and market timing.

Configuration Structure
-----------------------

A standard node JSON configuration includes:

1. **Top-Level Node Metadata**:
   * ``agentid``: Unique string identifier for the node.
   * ``name``: Human-readable node name.
   * ``market_cycle_in_min``: Negotiation period in minutes (e.g., 15 or 60).
   * ``duality_gap_threshold``: Convergence threshold for market clearing.

2. **Local Assets List** (``local_assets``):
   * Array of ``ModelFrameAsset`` objects with their sub-model configurations.

3. **Neighbors List** (``neighbors``):
   * Array of neighboring transactive agents and bulk power suppliers.

4. **Market Series List** (``market_series``):
   * Market series definition with horizon length, interval duration, and auction rules.

5. **Information Services List** (``information_services``):
   * Forecast models for weather, temperature, and solar irradiance.

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
