"""
Centralize Spark session creation.

Module Constraints
------------------

    - There is exactly one session builder function.
    - Application naming and configuration are deterministic.
    - The module is free of ingestion-specific logic.
"""

import logging

from pyspark.sql import SparkSession

from nyc_cab.config import RuntimeConfig
from nyc_cab.spark_config import SparkConfig


logger = logging.getLogger(__name__)


def build_spark_session(runtime_config: RuntimeConfig, spark_config: SparkConfig) -> SparkSession:
    """
    Create and configure a Spark session for platform workloads.

    Applies all platform-required Spark settings via :func:`apply_spark_config`,
    creates (or retrieves) the session, and sets the Spark log level from the
    runtime configuration.
    """
    logger.info(
        "Building Spark session: master=%s app_name=%s",
        spark_config.master, spark_config.app_name,
    )
    builder = apply_spark_config(SparkSession.builder, spark_config)
    session = builder.getOrCreate()
    session.sparkContext.setLogLevel(runtime_config.log_level)
    logger.info("Spark session ready, log level set to %s", runtime_config.log_level)
    return session


def apply_spark_config(builder: SparkSession.Builder, spark_config: SparkConfig) -> SparkSession.Builder:
    """
    Apply platform-required Spark settings to a session builder.

    Sets the Spark master, application name, and the dynamic partition
    overwrite mode required by the Bronze ingestion layer's idempotent
    write semantics.
    """
    return (
        builder
        .master(spark_config.master)
        .appName(spark_config.app_name)
        .config("spark.sql.sources.partitionOverwriteMode", "dynamic")
    )
