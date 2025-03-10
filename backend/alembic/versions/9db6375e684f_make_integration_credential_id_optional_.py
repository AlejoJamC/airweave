"""make integration credential id optional for connection

Revision ID: 9db6375e684f
Revises: d3969b3eb198
Create Date: 2025-02-03 19:32:07.330922

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '9db6375e684f'
down_revision = 'd3969b3eb198'
branch_labels = None
depends_on = None


def upgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('chat', 'sync_id',
               existing_type=sa.UUID(),
               nullable=True)
    op.alter_column('connection', 'integration_credential_id',
               existing_type=sa.UUID(),
               nullable=True)
    # ### end Alembic commands ###


def downgrade():
    # ### commands auto generated by Alembic - please adjust! ###
    op.alter_column('connection', 'integration_credential_id',
               existing_type=sa.UUID(),
               nullable=False)
    op.alter_column('chat', 'sync_id',
               existing_type=sa.UUID(),
               nullable=False)
    # ### end Alembic commands ###
