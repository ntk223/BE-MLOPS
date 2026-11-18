"""add chat_sessions and chat_messages tables

Revision ID: 4b5c6d7e8f90
Revises: 1de0e14023d0
Create Date: 2026-11-17 09:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = '4b5c6d7e8f90'
down_revision: str | None = '1de0e14023d0'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'chat_sessions',
        sa.Column('session_id', sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column('student_key', sa.CHAR(length=16), nullable=False),
        sa.Column('code_module', sa.String(length=8), nullable=False),
        sa.Column('code_presentation', sa.String(length=8), nullable=False),
        sa.Column('user_id', sa.BIGINT(), nullable=False),
        sa.Column('title', sa.String(length=255), nullable=False),
        sa.Column(
            'status',
            sa.String(length=24),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column('brief_id', sa.BIGINT(), nullable=True),
        sa.Column(
            'created_at',
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('active', 'finalized', 'archived')",
            name='check_chat_session_status',
        ),
        sa.ForeignKeyConstraint(['brief_id'], ['briefs.brief_id']),
        sa.ForeignKeyConstraint(['user_id'], ['users.user_id']),
        sa.PrimaryKeyConstraint('session_id'),
    )
    op.create_index(
        'idx_chat_sessions_student',
        'chat_sessions',
        ['code_module', 'code_presentation', 'student_key'],
        unique=False,
    )
    op.create_index(
        'idx_chat_sessions_user',
        'chat_sessions',
        ['user_id', 'status'],
        unique=False,
    )

    op.create_table(
        'chat_messages',
        sa.Column('message_id', sa.BIGINT(), autoincrement=True, nullable=False),
        sa.Column('session_id', sa.BIGINT(), nullable=False),
        sa.Column('role', sa.String(length=16), nullable=False),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column(
            'cited_evidence',
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            'tokens_used',
            sa.INTEGER(),
            server_default=sa.text('0'),
            nullable=False,
        ),
        sa.Column(
            'created_at',
            postgresql.TIMESTAMP(timezone=True),
            server_default=sa.text('now()'),
            nullable=False,
        ),
        sa.CheckConstraint(
            "role IN ('advisor', 'assistant')",
            name='check_chat_message_role',
        ),
        sa.ForeignKeyConstraint(
            ['session_id'],
            ['chat_sessions.session_id'],
            ondelete='CASCADE',
        ),
        sa.PrimaryKeyConstraint('message_id'),
    )
    op.create_index(
        'idx_chat_messages_session',
        'chat_messages',
        ['session_id', 'created_at'],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index('idx_chat_messages_session', table_name='chat_messages')
    op.drop_table('chat_messages')
    op.drop_index('idx_chat_sessions_user', table_name='chat_sessions')
    op.drop_index('idx_chat_sessions_student', table_name='chat_sessions')
    op.drop_table('chat_sessions')
