"""
Tests for the NYC Cabs Experimental Platform.

Test layout mirrors the package structure:

    nyc_cab/test/
        test_ingestion.py     # bronze ingestion and partitioning
        test_transform.py     # silver normalization and validation
        test_experiment.py    # cohort assignment determinism
        test_metrics.py       # gold aggregation correctness
        test_quality.py       # data contract validation

Run with:

    pytest nyc_cab/test/
"""
