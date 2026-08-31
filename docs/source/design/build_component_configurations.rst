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

2. **``local_assets`` List**:
   * Array of ``ModelFrameAsset`` objects with their sub-model configurations.

3. **``neighbors`` List**:
   * Array of neighboring transactive agents and bulk power suppliers.

4. **``market_series`` List**:
   * Market series definition with horizon length, interval duration, and auction rules.

5. **``information_services`` List**:
   * Forecast models for weather, temperature, and solar irradiance.
