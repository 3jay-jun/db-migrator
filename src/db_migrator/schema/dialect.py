from __future__ import annotations

from db_migrator.config.models import Dbms


def quote_identifier(dbms: Dbms, identifier: str) -> str:
    if dbms in {Dbms.MYSQL, Dbms.MARIADB}:
        return quote_mysql_identifier(identifier)
    if dbms is Dbms.POSTGRESQL:
        return quote_postgres_identifier(identifier)
    raise ValueError(f"Unsupported DBMS for identifier quoting: {dbms.value}")


def quote_mysql_identifier(identifier: str) -> str:
    return f"`{identifier.replace('`', '``')}`"


def quote_postgres_identifier(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


def qualified_table_name(dbms: Dbms, schema: str, table: str) -> str:
    return f"{quote_identifier(dbms, schema)}.{quote_identifier(dbms, table)}"
