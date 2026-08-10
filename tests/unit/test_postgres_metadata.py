from db_migrator.adapters.postgres import _format_source_type


def test_format_source_type_preserves_timestamp_precision() -> None:
    source_type = _format_source_type(
        {
            "data_type": "timestamp without time zone",
            "datetime_precision": 6,
            "character_maximum_length": None,
            "numeric_precision": None,
            "numeric_scale": None,
            "udt_name": "timestamp",
        }
    )

    assert source_type == "timestamp without time zone(6)"


def test_format_source_type_preserves_time_precision() -> None:
    source_type = _format_source_type(
        {
            "data_type": "time without time zone",
            "datetime_precision": 6,
            "character_maximum_length": None,
            "numeric_precision": None,
            "numeric_scale": None,
            "udt_name": "time",
        }
    )

    assert source_type == "time without time zone(6)"
