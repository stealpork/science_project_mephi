from alembic import op

revision = "b9142c89ea20"
down_revision = "3ee5b6bc2b69"
branch_labels = None
depends_on = None


def upgrade():
    op.create_index("ix_favorites_target", "favorites", ["target_type", "target_id"])


def downgrade():
    op.drop_index("ix_favorites_target", table_name="favorites")
