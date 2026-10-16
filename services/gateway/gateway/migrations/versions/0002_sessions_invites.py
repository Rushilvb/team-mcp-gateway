"""Dashboard sessions and invite links.

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "user_sessions",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_user_sessions"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_sessions_user_id_users", ondelete="CASCADE"),
        sa.UniqueConstraint("token_hash", name="uq_user_sessions_token_hash"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.add_column("users", sa.Column("invite_token_hash", sa.String(64)))
    op.add_column("users", sa.Column("invite_expires_at", sa.DateTime(timezone=True)))
    op.create_unique_constraint("uq_users_invite_token_hash", "users", ["invite_token_hash"])


def downgrade() -> None:
    op.drop_constraint("uq_users_invite_token_hash", "users", type_="unique")
    op.drop_column("users", "invite_expires_at")
    op.drop_column("users", "invite_token_hash")
    op.drop_table("user_sessions")
