import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "7a858aac4fc9"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():

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
        "project_audit",
        sa.Column("action_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("project_id", sa.String(length=36), nullable=False),
        sa.Column("action", sa.String(length=80), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("action_id"),
    )
    op.create_index(
        op.f("ix_project_audit_created_at"), "project_audit", ["created_at"], unique=False
    )
    op.create_table(
        "project_settings",
        sa.Column("name", sa.String(length=60), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    op.create_index(
        op.f("ix_project_settings_created_at"), "project_settings", ["created_at"], unique=False
    )
    op.create_table(
        "projects",
        sa.Column("creator_id", sa.String(length=36), nullable=False),
        sa.Column("creator_name", sa.String(length=200), nullable=False),
        sa.Column("owner_id", sa.String(length=36), nullable=True),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("summary", sa.String(length=700), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("research_goal", sa.Text(), nullable=False),
        sa.Column("area_id", sa.String(length=36), nullable=True),
        sa.Column("university_id", sa.String(length=36), nullable=True),
        sa.Column("interest_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_skill_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("required_roles", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "preferred_specialty_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column("eligible_courses", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("min_completed_projects", sa.Integer(), nullable=True),
        sa.Column("team_size", sa.Integer(), nullable=False),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_type", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("moderation_status", sa.String(length=20), nullable=False),
        sa.Column("results", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.CheckConstraint("team_size >= 1 AND team_size <= 100", name="valid_team_size"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_projects_access_type"), "projects", ["access_type"], unique=False)
    op.create_index(op.f("ix_projects_area_id"), "projects", ["area_id"], unique=False)
    op.create_index(op.f("ix_projects_created_at"), "projects", ["created_at"], unique=False)
    op.create_index(op.f("ix_projects_creator_id"), "projects", ["creator_id"], unique=False)
    op.create_index(
        op.f("ix_projects_moderation_status"), "projects", ["moderation_status"], unique=False
    )
    op.create_index(op.f("ix_projects_owner_id"), "projects", ["owner_id"], unique=False)
    op.create_index(op.f("ix_projects_status"), "projects", ["status"], unique=False)
    op.create_index(op.f("ix_projects_university_id"), "projects", ["university_id"], unique=False)
    op.create_table(
        "project_applications",
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("requested_role", sa.String(length=30), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("decision_reason", sa.Text(), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_applications_created_at"),
        "project_applications",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_applications_project_id"),
        "project_applications",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_applications_status"), "project_applications", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_project_applications_user_id"), "project_applications", ["user_id"], unique=False
    )
    op.create_index(
        "one_pending_application",
        "project_applications",
        ["project_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "project_file_reservations",
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("actor_id", sa.String(length=36), nullable=False),
        sa.Column("replaces_file_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_file_reservations_created_at"),
        "project_file_reservations",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_file_reservations_file_id"),
        "project_file_reservations",
        ["file_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_file_reservations_project_id"),
        "project_file_reservations",
        ["project_id"],
        unique=False,
    )
    op.create_table(
        "project_files",
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("file_id", sa.String(length=36), nullable=False),
        sa.Column("uploaded_by", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("origin", sa.String(length=20), nullable=False),
        sa.Column("paper_id", sa.String(length=36), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "file_id"),
    )
    op.create_index(
        op.f("ix_project_files_created_at"), "project_files", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_project_files_project_id"), "project_files", ["project_id"], unique=False
    )
    op.create_table(
        "project_invitations",
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("inviter_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_project_invitations_created_at"),
        "project_invitations",
        ["created_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_invitations_project_id"),
        "project_invitations",
        ["project_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_project_invitations_status"), "project_invitations", ["status"], unique=False
    )
    op.create_index(
        op.f("ix_project_invitations_user_id"), "project_invitations", ["user_id"], unique=False
    )
    op.create_index(
        "one_pending_invitation",
        "project_invitations",
        ["project_id", "user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "project_members",
        sa.Column("project_id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.Column("left_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", sa.Uuid(as_uuid=False), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "user_id"),
    )
    op.create_index(
        op.f("ix_project_members_created_at"), "project_members", ["created_at"], unique=False
    )
    op.create_index(
        op.f("ix_project_members_project_id"), "project_members", ["project_id"], unique=False
    )
    op.create_index(
        op.f("ix_project_members_user_id"), "project_members", ["user_id"], unique=False
    )


def downgrade():

    op.drop_index(op.f("ix_project_members_user_id"), table_name="project_members")
    op.drop_index(op.f("ix_project_members_project_id"), table_name="project_members")
    op.drop_index(op.f("ix_project_members_created_at"), table_name="project_members")
    op.drop_table("project_members")
    op.drop_index(
        "one_pending_invitation",
        table_name="project_invitations",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_project_invitations_user_id"), table_name="project_invitations")
    op.drop_index(op.f("ix_project_invitations_status"), table_name="project_invitations")
    op.drop_index(op.f("ix_project_invitations_project_id"), table_name="project_invitations")
    op.drop_index(op.f("ix_project_invitations_created_at"), table_name="project_invitations")
    op.drop_table("project_invitations")
    op.drop_index(op.f("ix_project_files_project_id"), table_name="project_files")
    op.drop_index(op.f("ix_project_files_created_at"), table_name="project_files")
    op.drop_table("project_files")
    op.drop_index(
        op.f("ix_project_file_reservations_project_id"), table_name="project_file_reservations"
    )
    op.drop_index(
        op.f("ix_project_file_reservations_file_id"), table_name="project_file_reservations"
    )
    op.drop_index(
        op.f("ix_project_file_reservations_created_at"), table_name="project_file_reservations"
    )
    op.drop_table("project_file_reservations")
    op.drop_index(
        "one_pending_application",
        table_name="project_applications",
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.drop_index(op.f("ix_project_applications_user_id"), table_name="project_applications")
    op.drop_index(op.f("ix_project_applications_status"), table_name="project_applications")
    op.drop_index(op.f("ix_project_applications_project_id"), table_name="project_applications")
    op.drop_index(op.f("ix_project_applications_created_at"), table_name="project_applications")
    op.drop_table("project_applications")
    op.drop_index(op.f("ix_projects_university_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_status"), table_name="projects")
    op.drop_index(op.f("ix_projects_owner_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_moderation_status"), table_name="projects")
    op.drop_index(op.f("ix_projects_creator_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_created_at"), table_name="projects")
    op.drop_index(op.f("ix_projects_area_id"), table_name="projects")
    op.drop_index(op.f("ix_projects_access_type"), table_name="projects")
    op.drop_table("projects")
    op.drop_index(op.f("ix_project_settings_created_at"), table_name="project_settings")
    op.drop_table("project_settings")
    op.drop_index(op.f("ix_project_audit_created_at"), table_name="project_audit")
    op.drop_table("project_audit")
    op.drop_table("processed_events")
    op.drop_table("outbox_events")
    op.drop_table("idempotency_keys")
