import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a8969cd5ddd8"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():

    op.create_table(
        "file_bindings",
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("resource_type", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("file_id", "resource_type", "resource_id"),
    )
    op.create_index(
        op.f("ix_file_bindings_created_at"), "file_bindings", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_file_bindings_file_id"), "file_bindings", ["file_id"], unique=False)
    op.create_index(
        op.f("ix_file_bindings_resource_id"), "file_bindings", ["resource_id"], unique=False
    )
    op.create_table(
        "files",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("filename", sa.String(length=200), nullable=False),
        sa.Column("content_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("checksum_sha256", sa.String(length=100), nullable=False),
        sa.Column("quarantine_key", sa.String(length=200), nullable=False),
        sa.Column("object_version", sa.String(length=200), nullable=True),
        sa.Column("final_key", sa.String(length=200), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rejection_code", sa.String(length=100), nullable=True),
        sa.Column("reservation_id", sa.String(length=36), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("final_key"),
        sa.UniqueConstraint("quarantine_key"),
    )
    op.create_index(op.f("ix_files_created_at"), "files", ["created_at"], unique=False)
    op.create_index(op.f("ix_files_resource_id"), "files", ["resource_id"], unique=False)
    op.create_index(op.f("ix_files_status"), "files", ["status"], unique=False)
    op.create_index(op.f("ix_files_user_id"), "files", ["user_id"], unique=False)
    op.create_table(
        "idempotency_keys",
        sa.Column("scope", sa.String(length=500), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("scope"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("event_type", sa.String(length=100), nullable=False),
        sa.Column("aggregate_id", sa.String(length=100), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "processed_events",
        sa.Column("event_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("consumer", sa.String(length=100), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("event_id", "consumer"),
    )


def downgrade():

    op.drop_table("processed_events")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_files_user_id"), table_name="files")
    op.drop_index(op.f("ix_files_status"), table_name="files")
    op.drop_index(op.f("ix_files_resource_id"), table_name="files")
    op.drop_index(op.f("ix_files_created_at"), table_name="files")
    op.drop_table("files")
    op.drop_index(op.f("ix_file_bindings_resource_id"), table_name="file_bindings")
    op.drop_index(op.f("ix_file_bindings_file_id"), table_name="file_bindings")
    op.drop_index(op.f("ix_file_bindings_created_at"), table_name="file_bindings")
    op.drop_table("file_bindings")
