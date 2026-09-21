import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c3c0c6bae707"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():

    op.create_table(
        "dictionary_entries",
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("parent_key", sa.String(length=36), nullable=False),
        sa.Column("skill_kind", sa.String(length=30), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "normalized_name", "parent_key"),
    )
    op.create_index(
        op.f("ix_dictionary_entries_created_at"), "dictionary_entries", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_dictionary_entries_kind"), "dictionary_entries", ["kind"], unique=False
    )
    op.create_table(
        "dictionary_suggestions",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("comment", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("resolved_id", sa.String(length=36), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_dictionary_suggestions_created_at"),
        "dictionary_suggestions",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_dictionary_suggestions_status"), "dictionary_suggestions", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_dictionary_suggestions_user_id"),
        "dictionary_suggestions",
        ["user_id"],
        unique=False,
    )
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
    op.create_table(
        "profiles",
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("first_name", sa.String(length=80), nullable=False),
        sa.Column("last_name", sa.String(length=80), nullable=False),
        sa.Column("bio", sa.Text(), nullable=False),
        sa.Column("university_id", sa.String(length=36), nullable=True),
        sa.Column("faculty_id", sa.String(length=36), nullable=True),
        sa.Column("department_id", sa.String(length=36), nullable=True),
        sa.Column("specialty_id", sa.String(length=36), nullable=True),
        sa.Column("city_id", sa.String(length=36), nullable=True),
        sa.Column("course", sa.Integer(), nullable=True),
        sa.Column("avatar_file_id", sa.String(length=36), nullable=True),
        sa.Column("skills", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("interests", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("experience", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("contacts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("blocked", sa.Boolean(), nullable=False),
        sa.Column("deleted", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_profiles_city_id"), "profiles", ["city_id"], unique=False)
    op.create_index(op.f("ix_profiles_created_at"), "profiles", ["created_at"], unique=False)
    op.create_index(op.f("ix_profiles_faculty_id"), "profiles", ["faculty_id"], unique=False)
    op.create_index(op.f("ix_profiles_specialty_id"), "profiles", ["specialty_id"], unique=False)
    op.create_index(op.f("ix_profiles_university_id"), "profiles", ["university_id"], unique=False)


def downgrade():

    op.drop_index(op.f("ix_profiles_university_id"), table_name="profiles")
    op.drop_index(op.f("ix_profiles_specialty_id"), table_name="profiles")
    op.drop_index(op.f("ix_profiles_faculty_id"), table_name="profiles")
    op.drop_index(op.f("ix_profiles_created_at"), table_name="profiles")
    op.drop_index(op.f("ix_profiles_city_id"), table_name="profiles")
    op.drop_table("profiles")
    op.drop_table("processed_events")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_keys")
    op.drop_index(op.f("ix_dictionary_suggestions_user_id"), table_name="dictionary_suggestions")
    op.drop_index(op.f("ix_dictionary_suggestions_status"), table_name="dictionary_suggestions")
    op.drop_index(op.f("ix_dictionary_suggestions_created_at"), table_name="dictionary_suggestions")
    op.drop_table("dictionary_suggestions")
    op.drop_index(op.f("ix_dictionary_entries_kind"), table_name="dictionary_entries")
    op.drop_index(op.f("ix_dictionary_entries_created_at"), table_name="dictionary_entries")
    op.drop_table("dictionary_entries")
