"""Check MySQL DDL without connecting to a database."""

import importlib.util
from io import StringIO
from pathlib import Path

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations


@pytest.mark.parametrize("direction,length", [("upgrade", 64), ("downgrade", 26)])
def test_request_id_migration_preserves_mysql_nullability(direction: str, length: int) -> None:
    path = (
        Path(__file__).resolve().parents[1]
        / "alembic/versions/0004_widen_trace_request_id.py"
    )
    spec = importlib.util.spec_from_file_location("trace_request_id_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)

    output = StringIO()
    context = MigrationContext.configure(
        dialect_name="mysql", opts={"as_sql": True, "output_buffer": output}
    )
    with Operations.context(context):
        getattr(migration, direction)()

    statements = [
        statement.strip() for statement in output.getvalue().split(";") if statement.strip()
    ]
    assert len(statements) == 2
    assert (
        f"ALTER TABLE query_traces MODIFY request_id VARCHAR({length}) NOT NULL"
        in statements
    )
    assert (
        f"ALTER TABLE evaluation_case_results MODIFY request_id VARCHAR({length}) NULL"
        in statements
    )
