"""Add optional tenant metadata columns

Revision ID: add_tenant_optional_fields
Revises: 53a1509a0256
Create Date: 2025-12-16 21:15:00.000000
"""

from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'add_tenant_optional_fields'
down_revision: Union[str, Sequence[str], None] = '53a1509a0256'
branch_labels = None
depends_on = None


def upgrade() -> None:
  op.add_column('tenants', sa.Column('industry', sa.String(length=255), nullable=True))
  op.add_column('tenants', sa.Column('team_size', sa.String(length=100), nullable=True))
  op.add_column('tenants', sa.Column('primary_use_case', sa.String(length=255), nullable=True))
  op.add_column('tenants', sa.Column('ehr_vendor', sa.String(length=255), nullable=True))
  op.add_column('tenants', sa.Column('region', sa.String(length=100), nullable=True))
  op.add_column('tenants', sa.Column('security_contact', sa.String(length=255), nullable=True))
  op.add_column('tenants', sa.Column('onboarding_notes', sa.Text(), nullable=True))


def downgrade() -> None:
  op.drop_column('tenants', 'onboarding_notes')
  op.drop_column('tenants', 'security_contact')
  op.drop_column('tenants', 'region')
  op.drop_column('tenants', 'ehr_vendor')
  op.drop_column('tenants', 'primary_use_case')
  op.drop_column('tenants', 'team_size')
  op.drop_column('tenants', 'industry')
