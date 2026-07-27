"""PostgreSQL model metadata tests that do not open a database connection."""

from sqlalchemy import CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB

from app.models import AutomationJob, Base, BitrixPortal


def test_metadata_contains_expected_tables_and_naming_convention() -> None:
    """All tables share deterministic constraint naming rules."""
    assert set(Base.metadata.tables) == {"bitrix_portals", "automation_jobs"}
    assert set(Base.metadata.naming_convention) >= {"ix", "uq", "ck", "fk", "pk"}


def test_portal_columns_and_constraints() -> None:
    """Portal identity, authentication, and lifecycle metadata match the schema contract."""
    table = BitrixPortal.__table__
    assert table.name == "bitrix_portals"
    assert table.c.member_id.nullable is False
    assert table.c.domain.nullable is False
    assert table.c.server_endpoint.nullable is True
    assert table.c.created_at.type.timezone is True
    assert table.c.updated_at.type.timezone is True
    assert any(
        isinstance(constraint, UniqueConstraint)
        and {column.name for column in constraint.columns} == {"member_id"}
        for constraint in table.constraints
    )
    assert any(
        isinstance(constraint, CheckConstraint) and "auth_error" in str(constraint.sqltext)
        for constraint in table.constraints
    )
    assert {index.name for index in table.indexes} == {"ix_bitrix_portals_status"}


def test_job_columns_constraints_foreign_key_and_indexes() -> None:
    """Job metadata preserves idempotency, retry limits, JSONB, and due-job lookup."""
    table = AutomationJob.__table__
    assert table.name == "automation_jobs"
    assert table.c.portal_id.nullable is False
    assert table.c.event_token_encrypted.nullable is False
    assert table.c.run_at.type.timezone is True
    assert isinstance(table.c.payload.type, JSONB)
    assert isinstance(table.c.return_values.type, JSONB)
    assert {foreign_key.target_fullname for foreign_key in table.c.portal_id.foreign_keys} == {
        "bitrix_portals.id"
    }

    unique_columns = {
        tuple(column.name for column in constraint.columns)
        for constraint in table.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    assert ("portal_id", "event_token_hash") in unique_columns

    checks = {
        str(constraint.sqltext)
        for constraint in table.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert {"attempts >= 0", "max_attempts >= 1", "attempts <= max_attempts"} <= checks
    assert any("cancelled" in expression for expression in checks)

    indexes = {index.name: index for index in table.indexes}
    assert set(indexes) == {"ix_automation_jobs_due", "ix_automation_jobs_portal_id"}
    due_predicate = indexes["ix_automation_jobs_due"].dialect_options["postgresql"]["where"]
    assert str(due_predicate) == "status IN ('pending', 'retry')"
