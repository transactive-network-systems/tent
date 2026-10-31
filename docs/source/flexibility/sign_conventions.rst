.. _flexibility_sign_conventions:

=======================================================
Sign Conventions: Load-Positive vs. Generation-Positive
=======================================================

Different domains within power systems and software engineering use conflicting sign conventions for electrical power. To eliminate ambiguity, TENT enforces strict convention handling at all boundaries.

The Two Conventions
-------------------

.. list-table::
   :widths: 25 35 40
   :header-rows: 1

   * - Convention
     - Physical Meaning
     - Primary Usage
   * - ``LOAD_POSITIVE`` (Canonical FMI)
     - | :math:`P > 0` : Consumption / Charging
       | :math:`P < 0` : Generation / Discharging
     - Physical simulators (OCHRE, GridLAB-D, pvlib, physical meters)
   * - ``GENERATION_POSITIVE`` (TENT Internal)
     - | :math:`P > 0` : Generation / Production
       | :math:`P < 0` : Demand / Consumption
     - TENT ``ModelFrameAsset``, ``Market``, ``Auction``

Boundary Conversion Rule
------------------------

When physical simulator data passes into TENT's ``FlexibilityModelFrameAdapter``, values are converted via canonical sign inversion:

.. math::

   P_{\text{TENT}} = -P_{\text{FMI}}

   P_{\min, \text{TENT}} = -P_{\max, \text{FMI}}

   P_{\max, \text{TENT}} = -P_{\min, \text{FMI}}

This transformation guarantees that sorting orders and supply/demand bid monotonicity are strictly preserved.
