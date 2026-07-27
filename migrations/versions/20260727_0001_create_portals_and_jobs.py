"""Create Bitrix portal and automation job tables.

Revision ID: 20260727_0001
Revises: None
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260727_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the initial PostgreSQL persistence schema."""
    op.create_table(
        "bitrix_portals",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("member_id", sa.String(length=64), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("client_endpoint", sa.String(length=500), nullable=False),
        sa.Column("server_endpoint", sa.String(length=500), nullable=True),
        sa.Column("access_token_encrypted", sa.Text(), nullable=True),
        sa.Column("refresh_token_encrypted", sa.Text(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("application_token_hash", sa.String(length=128), nullable=True),
        sa.Column("status", sa.String(length=30), server_default="active", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive', 'auth_error')",
            name="ck_bitrix_portals_portal_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_bitrix_portals"),
        sa.UniqueConstraint("member_id", name="uq_bitrix_portals_member_id"),
    )
    op.create_index("ix_bitrix_portals_status", "bitrix_portals", ["status"])

    op.create_table(
        "automation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("portal_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("robot_code", sa.String(length=100), nullable=False),
        sa.Column("event_token_encrypted", sa.Text(), nullable=False),
        sa.Column("event_token_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "return_values",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=30), server_default="pending", nullable=False),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="10", nullable=False),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("locked_by", sa.String(length=100), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_automation_jobs_attempts_non_negative"),
        sa.CheckConstraint(
            "attempts <= max_attempts", name="ck_automation_jobs_attempts_within_limit"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry', 'completed', 'failed', "
            "'expired', 'cancelled')",
            name="ck_automation_jobs_job_status",
        ),
        sa.CheckConstraint("max_attempts >= 1", name="ck_automation_jobs_max_attempts_positive"),
        sa.ForeignKeyConstraint(
            ["portal_id"], ["bitrix_portals.id"], name="fk_automation_jobs_portal_id_bitrix_portals"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_automation_jobs"),
        sa.UniqueConstraint(
            "portal_id",
            "event_token_hash",
            name="uq_automation_jobs_portal_id_event_token_hash",
        ),
    )
    op.create_index("ix_automation_jobs_portal_id", "automation_jobs", ["portal_id"])
    op.create_index(
        "ix_automation_jobs_due",
        "automation_jobs",
        ["run_at"],
        postgresql_where=sa.text("status IN ('pending', 'retry')"),
    )


def downgrade() -> None:
    """Drop the initial schema in dependency-safe reverse order."""
    op.drop_index("ix_automation_jobs_due", table_name="automation_jobs")
    op.drop_index("ix_automation_jobs_portal_id", table_name="automation_jobs")
    op.drop_table("automation_jobs")
    op.drop_index("ix_bitrix_portals_status", table_name="bitrix_portals")
    op.drop_table("bitrix_portals")
