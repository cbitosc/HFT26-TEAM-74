"""ProofStack portfolio sessions."""
from alembic import op
import sqlalchemy as sa
revision = '0013_portfolio_sessions'
down_revision = '0012_concept_question_types'
branch_labels = None
depends_on = None
def upgrade():
    op.create_table('portfolio_sessions', sa.Column('id',sa.Integer(),primary_key=True), sa.Column('token',sa.String(100),nullable=False), sa.Column('state',sa.JSON(),nullable=False), sa.Column('created_at',sa.DateTime(timezone=True),server_default=sa.func.now(),nullable=False))
    op.create_index('ix_portfolio_sessions_token','portfolio_sessions',['token'],unique=True)
def downgrade():
    op.drop_table('portfolio_sessions')
