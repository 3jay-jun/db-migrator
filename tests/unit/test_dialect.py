from db_migrator.config.models import Dbms
from db_migrator.schema.dialect import qualified_table_name, quote_identifier, quote_mysql_identifier, quote_postgres_identifier


def test_quote_mysql_identifier_escapes_backticks() -> None:
    assert quote_mysql_identifier("odd`name") == "`odd``name`"
    assert quote_identifier(Dbms.MYSQL, "odd`name") == "`odd``name`"
    assert quote_identifier(Dbms.MARIADB, "odd`name") == "`odd``name`"


def test_quote_postgres_identifier_escapes_double_quotes() -> None:
    assert quote_postgres_identifier('odd"name') == '"odd""name"'
    assert quote_identifier(Dbms.POSTGRESQL, 'odd"name') == '"odd""name"'


def test_qualified_table_name_uses_dbms_quoting() -> None:
    assert qualified_table_name(Dbms.MYSQL, "target`db", "orders") == "`target``db`.`orders`"
    assert qualified_table_name(Dbms.POSTGRESQL, "public", 'odd"name') == '"public"."odd""name"'
