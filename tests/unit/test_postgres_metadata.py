from db_migrator.adapters.postgres import _format_source_type, _is_postgres_auto_increment_column
from db_migrator.schema.type_mapping import postgres_type_to_common


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


def test_postgres_nextval_single_integer_primary_key_is_auto_increment() -> None:
    assert _is_postgres_auto_increment_column(
        {
            "column_name": "id",
            "column_default": "nextval('privacy_body_id_seq'::regclass)",
            "is_identity": "NO",
        },
        common_type=postgres_type_to_common("bigint"),
        primary_key_columns=("id",),
    )


def test_postgres_nextval_non_primary_key_is_not_auto_increment() -> None:
    assert not _is_postgres_auto_increment_column(
        {
            "column_name": "invoice_no",
            "column_default": "nextval('invoice_no_seq'::regclass)",
            "is_identity": "NO",
        },
        common_type=postgres_type_to_common("bigint"),
        primary_key_columns=("id",),
    )


def test_postgres_identity_single_integer_primary_key_is_auto_increment() -> None:
    assert _is_postgres_auto_increment_column(
        {
            "column_name": "id",
            "column_default": None,
            "is_identity": "YES",
        },
        common_type=postgres_type_to_common("integer"),
        primary_key_columns=("id",),
    )
