"""
Define the NYC Cab data ingestion package.

Bronze ingestion is split across focused modules:

- `bronze_request` defines typed ingestion inputs and Bronze-specific settings.
- `source_resolver` derives deterministic source URLs and filesystem paths.
- `bronze_io` handles cache-aware source acquisition.
- `bronze` composes the ingestion workflow, validates the raw schema, and
  writes deterministic Bronze output.

Spark session creation remains outside this package in `nyc_cab.orchestration.spark`
so ingestion logic never creates ad hoc sessions.
"""
