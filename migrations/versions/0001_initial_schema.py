"""
建立当前数据库架构基线。

Revision ID: 1
Revises:
"""

from __future__ import annotations

# noinspection package-requirements
from alembic import op
import sqlalchemy as sa

revision: str = "1"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: str | None = None


def upgrade() -> None:
    """创建当前版本的全部业务表和整数架构版本表。"""
    op.create_table(
        "containers",
        sa.Column("container_id", sa.String(length=64), nullable=False),
        sa.Column("image", sa.String(length=512), nullable=False),
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.Column("gitee_user", sa.String(length=128), nullable=False),
        sa.Column("gitee_repository", sa.String(length=128), nullable=False),
        sa.Column("gitee_branch", sa.String(length=128), nullable=True),
        sa.Column("gitee_url", sa.String(length=512), nullable=False),
        sa.Column("authorize_general_account", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.String(length=32), nullable=False),
        sa.Column("expiration_hours", sa.Integer(), nullable=False),
        sa.Column("deleted_at", sa.String(length=32), nullable=True),
        sa.PrimaryKeyConstraint("container_id"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("value", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "whitelist_users",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "admin_users",
        sa.Column("user_id", sa.String(length=128), nullable=False),
        sa.PrimaryKeyConstraint("user_id"),
    )
    op.create_table(
        "schema_version",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("id = 1", name="ck_schema_version_single_row"),
        sa.CheckConstraint("version >= 1", name="ck_schema_version_positive"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.execute(sa.text("INSERT INTO schema_version (id, version) VALUES (1, 1)"))


def downgrade() -> None:
    op.drop_table("schema_version")
    op.drop_table("admin_users")
    op.drop_table("whitelist_users")
    op.drop_table("settings")
    op.drop_table("containers")
