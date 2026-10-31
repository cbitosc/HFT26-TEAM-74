"""Persistent ProofStack daily quotas."""
from alembic import op
import sqlalchemy as sa
revision='0014_portfolio_usage'
down_revision='0013_portfolio_sessions'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('portfolio_usage',sa.Column('key',sa.String(100),primary_key=True),sa.Column('period',sa.String(10),primary_key=True),sa.Column('used',sa.Integer(),nullable=False))

def downgrade():
    op.drop_table('portfolio_usage')
