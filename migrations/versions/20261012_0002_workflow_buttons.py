"""Add workflow field settings and durable launch journal."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "20261012_0002"
down_revision = "20260727_0001"
branch_labels = None
depends_on = None


def timestamps():
    return [
        sa.Column(name, sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)
        for name in ("created_at", "updated_at")
    ]


def upgrade():
    op.create_table(
        "workflow_buttons",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "portal_id", UUID(as_uuid=True), sa.ForeignKey("bitrix_portals.id"), nullable=False
        ),
        sa.Column("field_name", sa.String(100), nullable=False),
        sa.Column("settings", JSONB(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("portal_id", "field_name"),
    )
    op.create_table(
        "workflow_launches",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "portal_id", UUID(as_uuid=True), sa.ForeignKey("bitrix_portals.id"), nullable=False
        ),
        sa.Column(
            "button_id", UUID(as_uuid=True), sa.ForeignKey("workflow_buttons.id"), nullable=False
        ),
        sa.Column("request_id", UUID(as_uuid=True), nullable=False),
        sa.Column("deal_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(30), nullable=False),
        sa.Column("workflow_id", sa.String(100)),
        sa.Column("released_at", sa.DateTime(timezone=True)),
        sa.Column("released_by", sa.Integer()),
        *timestamps(),
        sa.UniqueConstraint("portal_id", "request_id"),
    )
    op.create_index(
        "ix_workflow_launches_button_deal",
        "workflow_launches",
        ["button_id", "deal_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_workflow_launches_button_deal", table_name="workflow_launches")
    op.drop_table("workflow_launches")
    op.drop_table("workflow_buttons")
