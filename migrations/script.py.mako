"""${message}

Revision ID: ${up_revision}
Revises: ${down_revision | comma,n}
Create Date: ${create_date}
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = ${repr(up_revision)}
down_revision: Union[str, Sequence[str], None] = ${repr(down_revision)}
branch_labels: Union[str, Sequence[str], None] = ${repr(branch_labels)}
depends_on: Union[str, Sequence[str], None] = ${repr(depends_on)}


def _set_schema_version(version: int) -> None:
    op.execute(f"UPDATE schema_version SET version = {version} WHERE id = 1")


def upgrade() -> None:
    # Use the next integer revision ID (2, 3, ...) for schema changes.
    version = int(revision)
    ${upgrades if upgrades else "pass"}
    _set_schema_version(version)


def downgrade() -> None:
    ${downgrades if downgrades else "pass"}
    if down_revision is not None:
        _set_schema_version(int(down_revision))
