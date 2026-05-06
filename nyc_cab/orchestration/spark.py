"""
Centralize Spark session creation.

Module Constraints
------------------

    - There is exactly one session builder function.
    - Application naming and configuration are deterministic.
    - The module is free of ingestion-specific logic.


"""

from pyspark.sql import SparkSession

from nyc_cab.config import RuntimeConfig
from nyc_cab.spark_config import SparkConfig


def build_spark_session(runtime_config: RuntimeConfig, spark_config: SparkConfig) -> SparkSession:
    """Create and configure a Spark session for platform workloads."""
    raise NotImplementedError


def apply_spark_config(builder: SparkSession.Builder, spark_config: SparkConfig) -> SparkSession.Builder:
    """Apply configured Spark settings to a Spark session builder."""
    raise NotImplementedError
