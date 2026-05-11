"""
Define the NYC Cab data ingestion package.

Bronze ingestion is split across focused modules:

- `bronze_request` defines typed ingestion inputs and Bronze-specific settings.
- `source_resolver` derives deterministic source URLs and filesystem paths.
- `bronze_io` handles cache-aware source acquisition.
- `bronze` composes the ingestion workflow, validates the raw schema, and
  writes deterministic Bronze output.

The bronze ingestion logic flows as described in this sequence diagram.

.. mermaid::

    sequenceDiagram
        autonumber
        participant Caller as CLI / Airflow
        participant Entry as bronze_entrypoint
        participant Contract as contracts.bronze
        participant Resolver as source_resolver
        participant IO as bronze_io
        participant Spark as SparkSession
        participant FS as Filesystem

        Caller->>Entry: ingest_bronze_month(spark, runtime_cfg, ingestion_cfg, request)

        Note over Entry,Contract: Step 1: semantic validation
        Entry->>Contract: validate_supported_bronze_slice(cab_type, year, month)
        Contract-->>Entry: ok (or raise InvalidRequestError)

        Note over Entry,Resolver: Step 2: pure path resolution
        Entry->>Resolver: resolve_bronze_paths(runtime_cfg, request)
        Resolver->>Contract: derive_bronze_source_url(...)
        Contract-->>Resolver: source_url
        Resolver->>Contract: derive_bronze_source_filename(...)
        Contract-->>Resolver: source_filename
        Resolver-->>Entry: BronzeResolvedPaths

        Note over Entry,IO: Step 3: cache-aware acquisition
        Entry->>IO: acquire_bronze_source_file(source_url, filename, ingestion_cfg)
        alt cache hit
            IO->>FS: read cached file
            FS-->>IO: local_path
        else cache miss
            IO->>FS: download to cache_directory
            FS-->>IO: local_path
            IO->>IO: evict_files() if over max_files
        end
        IO-->>Entry: AcquiredSourceFile

        Note over Entry,Spark: Step 4: schema validation
        Entry->>Spark: spark.read.parquet(local_path)
        Spark->>FS: read source parquet
        FS-->>Spark: DataFrame
        Spark-->>Entry: df
        Entry->>Entry: extract column names, types, nullability
        Entry->>Contract: validate_against_bronze_schema(cab_type, names, types, nullable)
        Contract-->>Entry: ok (or raise InvalidRequestError)

        Note over Entry,Spark: Step 5: partitioned write and result
        Entry->>Entry: df.withColumn(cab_type/year/month as literals)
        Entry->>Spark: df.write.partitionBy(...).parquet(bronze_root_path)
        Spark->>FS: write to {root}/cab_type=X/year=Y/month=Z/
        Entry->>Spark: df.count()
        Spark-->>Entry: row_count
        Entry->>Entry: BronzeIngestionResult.create_validated(...)
        Entry-->>Caller: BronzeIngestionResult


Spark session creation remains outside this package in `nyc_cab.orchestration.spark`
so ingestion logic never creates ad hoc sessions.
"""
