"""GotParking weekly LightGBM training job (design doc T5).

Loads live carpark history, excludes cold-start carparks, builds a
momentum/label feature set matching the serving contract in
``api/_lib/features.py`` bit-for-bit, optionally pretrains on the SINPA
historical dataset, trains a LightGBM candidate, backtests it leakage-free
against a historical-average baseline and a persistence benchmark on a
held-out window of the most recent live data, and promotes it via
``model_config`` when it clears the two-phase gate (design doc Premise #7,
amended).

See the design doc's Premise #7 (promotion gate), Premise #2 (features,
including momentum), Premise #10 (cold-start exclusion), Premise #1
(SINPA amendment), Approach C's T0 outcome, and the Failure Modes registry
for the full specification this package implements.
"""

from __future__ import annotations
