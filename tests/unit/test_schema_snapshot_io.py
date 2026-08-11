import json

from db_migrator.config.models import Dbms
from db_migrator.schema.common_types import CommonTypeKind, TypePolicy
from db_migrator.schema.snapshot_io import load_schema_snapshot_from_json


def test_load_schema_snapshot_uses_source_dbms_for_missing_common_type(tmp_path) -> None:
    snapshot_path = tmp_path / "mysql_snapshot.json"
    snapshot_path.write_text(
        json.dumps(
            {
                "tables": [
                    {
                        "schema": "legacy_db",
                        "name": "accounts",
                        "columns": [
                            {
                                "name": "id",
                                "source_type": "int unsigned",
                                "nullable": False,
                                "ordinal_position": 1,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    snapshot = load_schema_snapshot_from_json(snapshot_path, source_dbms=Dbms.MYSQL)

    common_type = snapshot.tables[0].columns[0].common_type
    assert common_type.kind is CommonTypeKind.BIGINT
    assert common_type.policy is TypePolicy.WARN_CONVERT
    assert common_type.warnings[0].code == "mysql_unsigned_warning"
