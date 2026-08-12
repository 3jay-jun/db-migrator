from db_migrator.application import ColumnSelection
from db_migrator.config.models import Dbms
from db_migrator.gui import main as gui_main
from db_migrator.schema.common_types import CommonType, CommonTypeKind, TypePolicy


def test_ddl_preview_adds_same_name_source_only_column_for_existing_target() -> None:
    ddls = gui_main._draft_mapped_column_change_candidates(
        "hd_bb",
        "account2",
        Dbms.MYSQL,
        (
            _column("id", "bigint", CommonTypeKind.BIGINT),
            _column("legacy_code", "text", CommonTypeKind.TEXT),
        ),
        (_column("id", "bigint", CommonTypeKind.BIGINT),),
        {"id": "id", "legacy_code": "legacy_code"},
        {},
    )

    assert ddls == ["ALTER TABLE `hd_bb`.`account2` ADD COLUMN `legacy_code` longtext NOT NULL;"]


def _column(name: str, source_type: str, kind: CommonTypeKind) -> ColumnSelection:
    return ColumnSelection(
        name=name,
        source_type=source_type,
        common_type=CommonType(kind=kind, policy=TypePolicy.AUTO_CONVERT),
        nullable=False,
        primary_key=name == "id",
    )
