"""Initial schema: users, api_keys, upstream_servers, audit_log.

Revision ID: 0001
Revises:
"""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("role", sa.String(16), nullable=False),
        sa.Column("password_hash", sa.Text()),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
        sa.CheckConstraint("role IN ('admin', 'member')", name="ck_users_role_valid"),
        sa.CheckConstraint("status IN ('active', 'invited', 'disabled')", name="ck_users_status_valid"),
    )
    op.create_table(
        "api_keys",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.Column("last_used_at", sa.DateTime(timezone=True)),
        sa.PrimaryKeyConstraint("id", name="pk_api_keys"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_api_keys_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("key_hash", name="uq_api_keys_key_hash"),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_table(
        "upstream_servers",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("slug", sa.String(32), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("auth_type", sa.String(16), nullable=False),
        sa.Column("header_name", sa.String(100)),
        sa.Column("credential_encrypted", sa.Text()),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("last_error", sa.Text()),
        sa.Column("last_checked_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id", name="pk_upstream_servers"),
        sa.UniqueConstraint("slug", name="uq_upstream_servers_slug"),
        sa.CheckConstraint("auth_type IN ('none', 'bearer', 'header')", name="ck_upstream_servers_auth_type_valid"),
        sa.CheckConstraint("status IN ('unknown', 'connected', 'unreachable', 'disabled')", name="ck_upstream_servers_status_valid"),
        sa.CheckConstraint("auth_type <> 'header' OR header_name IS NOT NULL", name="ck_upstream_servers_header_name_required"),
    )
    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True),
        sa.Column("user_id", sa.Integer()),
        sa.Column("channel", sa.String(8), nullable=False),
        sa.Column("server_slug", sa.String(32)),
        sa.Column("tool", sa.String(200), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column("outcome", sa.String(16), nullable=False),
        sa.Column("error_message", sa.Text()),
        sa.PrimaryKeyConstraint("id", name="pk_audit_log"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_audit_log_user_id_users", ondelete="SET NULL"),
        sa.CheckConstraint("channel IN ('api', 'chat')", name="ck_audit_log_channel_valid"),
        sa.CheckConstraint("outcome IN ('success', 'error', 'timeout', 'denied')", name="ck_audit_log_outcome_valid"),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_started_at", "audit_log", ["started_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("upstream_servers")
    op.drop_table("api_keys")
    op.drop_table("users")
