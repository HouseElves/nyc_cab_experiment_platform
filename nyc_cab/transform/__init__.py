"""
Define the NYC Cab data transformation package.

Silver transformation is split across focused modules:

- ``silver_request`` defines typed transform inputs.
- ``silver_transforms`` applies pure column normalizations (type casts).
- ``silver_validators`` applies domain constraints and tags rejection reasons.
- ``silver_entrypoint`` composes the transformation workflow: read Bronze,
  validate, normalize, validate again, split accepted/rejected, write both
  partitions.

The Silver transformation logic flows as described in this sequence diagram.

.. mermaid::

    sequenceDiagram
        autonumber
        participant Caller as CLI / Airflow
        participant Entry as silver_entrypoint
        participant Contract as contracts.silver
        participant Validators as silver_validators
        participant Transforms as silver_transforms
        participant Spark as SparkSession
        participant FS as Filesystem

        Caller->>Entry: transform_silver_month(spark, runtime_cfg, request)

        Note over Entry,Contract: Step 1: semantic validation
        Entry->>Contract: validate_supported_silver_slice(cab_type, year, month)
        Contract-->>Entry: ok (or raise InvalidRequestError)

        Note over Entry,Spark: Step 2: read Bronze partition
        Entry->>Spark: spark.read.parquet(bronze_partition_path)
        Spark->>FS: read cab_type=X/year=Y/month=Z/
        FS-->>Spark: DataFrame
        Spark-->>Entry: df (Bronze-typed columns)

        Note over Entry,Spark: Step 3: anchor reconciliation invariant
        Entry->>Spark: df.count()
        Spark-->>Entry: bronze_count

        Note over Entry,Validators: Step 4: pre-normalization constraints
        Entry->>Validators: apply_pre_normalization_constraints(df)
        Note over Validators: Initialize _rejection_reasons as empty array.<br/>Tag non-integral normalization targets.<br/>Tag nulls in constraint-checked fields.
        Validators-->>Entry: df (Bronze-typed + _rejection_reasons)

        Note over Entry,Transforms: Step 5: type normalization
        Entry->>Transforms: apply_type_normalizations(df)
        Note over Transforms: Cast per SILVER_YELLOW_TYPE_NORMALIZATIONS.<br/>passenger_count double→int, RatecodeID double→int.<br/>Safe: non-integral values are already tagged.
        Transforms-->>Entry: df (Silver-typed + _rejection_reasons)

        Note over Entry,Validators: Step 6: post-normalization constraints
        Entry->>Validators: apply_post_normalization_constraints(df)
        Note over Validators: Append to _rejection_reasons.<br/>Check fare≥0, distance≥0,<br/>passenger_count 0–9, pickup<dropoff.
        Validators-->>Entry: df (Silver-typed, fully tagged)

        Note over Entry,Validators: Step 7: split accepted and rejected
        Entry->>Validators: split_accepted_rejected(df)
        Note over Validators: Empty _rejection_reasons → accepted (column dropped).<br/>Non-empty → rejected (column retained).
        Validators-->>Entry: (accepted_df, rejected_df)

        Note over Entry,FS: Step 8: write accepted partition
        Entry->>Spark: accepted_df.write.partitionBy(...).parquet(silver_root)
        Spark->>FS: write to silver/cab_type=X/year=Y/month=Z/

        Note over Entry,FS: Step 9: write rejected partition
        Entry->>Spark: rejected_df.write.partitionBy(...).parquet(silver_rejected_root)
        Spark->>FS: write to silver_rejected/cab_type=X/year=Y/month=Z/

        Note over Entry,Spark: Step 10: count outputs
        Entry->>Spark: accepted_df.count(), rejected_df.count()
        Spark-->>Entry: accepted_count, rejected_count

        Note over Entry: Step 11: enforce reconciliation invariant
        Entry->>Entry: SilverTransformResult.create_validated(...)
        Note over Entry: Structural check: bronze_count == accepted_count + rejected_count.<br/>Raises InvalidRequestError if violated.
        Entry-->>Caller: SilverTransformResult


Spark session creation remains outside this package in ``nyc_cab.orchestration.spark``
so transformation logic never creates ad hoc sessions.
"""
