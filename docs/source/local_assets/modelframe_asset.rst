.. _local_assets_modelframe_asset:

============================================
ModelFrameAsset Architecture & Aggregation
============================================

``ModelFrameAsset`` is TENT's native composite asset model, combining the market bidding capabilities of ``LocalAsset`` with the multi-model composition engine of ``ModelFrame``.

Compositional Hierarchy
-----------------------

Rather than treating an entire commercial building or facility as a black box with a single rigid model, ``ModelFrameAsset`` hosts an arbitrary collection of sub-models within its ``models`` dictionary:

* **Thermostat Models (``tent.local_asset.model_frame.models.thermostat``)**: Second-order equivalent thermal parameter (RC) models for heating and cooling.
* **Lighting Models (``tent.local_asset.model_frame.models.light``)**: Illuminance and schedule-driven lighting power.
* **Uncontrolled Models (``tent.local_asset.model_frame.models.uncontrolled``)**: Plug loads, appliances, and non-shiftable baseload.
* **Physical FMI Models (``tent.local_asset.model_frame.models.fmi_adapter``)**: Physics-backed models powered by OCHRE, GridLAB-D, pvlib, or Replay datasets.

Aggregation Mechanics
---------------------

During market scheduling:

1. **Scheduled Power Calculation**:
   ``ModelFrameAsset.schedule_power(market)`` invokes ``self.model_power(params)`` on the underlying ``ModelFrame``, which sums the predicted baseline power across all active sub-models:

   .. math::

      P_{\text{asset}}(t) = \sum_{m \in \text{models}} P_m(t)

2. **Flexibility Aggregation**:
   ``ModelFrameAsset.update_vertices(market)`` queries ``self.model_flexibility(params)`` to aggregate the power bounds :math:`[P_{\min, \text{asset}}, P_{\max, \text{asset}}]` across all sub-models:

   .. math::

      P_{\min, \text{asset}}(t) = \sum_{m \in \text{models}} P_{\min, m}(t), \quad P_{\max, \text{asset}}(t) = \sum_{m \in \text{models}} P_{\max, m}(t)

3. **Vertex Generation**:
   The aggregated power range is combined with the market's price model to produce active supply/demand vertices :math:`(P, Q)` ready for double auction clearing.

4. **Closed-Loop Actuation**:
   Upon market clearance, ``ModelFrameAsset.actuate(market)`` iterates through its sub-models and calls ``model.actuate(self, market)``, dispatching cleared setpoints and power targets to the physical simulators.
