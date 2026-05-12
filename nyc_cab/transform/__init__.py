"""
Define the NYC Cab data transformation package.

Silver transformation is split across focused modules:

- ``silver_request`` defines typed transform inputs.
- ``silver_transforms`` applies pure column normalizations (type casts,
  derived columns).
- ``silver_validators`` applies domain constraints and tags rejection
  reasons on each row.
- ``silver_entrypoint`` composes the transformation workflow: read Bronze,
  normalize, validate, split accepted/rejected, write both partitions.

Spark session creation remains outside this package in
``nyc_cab.orchestration.spark`` so transformation logic never creates
ad hoc sessions.
"""
