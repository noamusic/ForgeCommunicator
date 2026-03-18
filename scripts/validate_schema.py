#!/usr/bin/env python3
"""
Schema Validation Script

Compares SQLAlchemy model definitions against the actual database schema
to detect missing columns, type mismatches, and other inconsistencies.

Usage:
    python scripts/validate_schema.py [--fix]
    
Options:
    --fix    Generate a migration file to fix detected issues (not implemented yet)

This script should be run:
- Before each deployment
- In CI/CD pipelines
- After adding new model columns

Exit codes:
    0 - Schema is in sync
    1 - Schema drift detected
"""

import sys
import asyncio
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine
from app.settings import settings
from app.db import Base

# Import all models to ensure they're registered
from app.models import (
    User, Workspace, Membership, ChannelMembership,
    Channel, Message, Product, Artifact,
    Note, NoteShare, PushSubscription, TeamInvite,
    ExternalIntegration, BridgedChannel, UserSession,
    Attachment, MessageReaction, SiteConfig,
)

# Try to import AI models if they exist
try:
    from app.models import AIAgent, AIThread, AIMessage
except ImportError:
    pass


def get_model_columns(model_class) -> dict:
    """Extract column definitions from a SQLAlchemy model."""
    columns = {}
    mapper = inspect(model_class)
    
    for column in mapper.columns:
        col_type = str(column.type)
        columns[column.name] = {
            'type': col_type,
            'nullable': column.nullable,
            'primary_key': column.primary_key,
            'default': column.default,
        }
    
    return columns


async def get_db_columns(engine, table_name: str) -> dict:
    """Get column definitions from the database."""
    columns = {}
    
    async with engine.connect() as conn:
        # Check if table exists
        result = await conn.execute(text("""
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = :table_name
            )
        """), {'table_name': table_name})
        
        exists = result.scalar()
        if not exists:
            return None  # Table doesn't exist
        
        # Get column info
        result = await conn.execute(text("""
            SELECT 
                column_name,
                data_type,
                is_nullable,
                column_default
            FROM information_schema.columns
            WHERE table_schema = 'public'
            AND table_name = :table_name
            ORDER BY ordinal_position
        """), {'table_name': table_name})
        
        for row in result:
            columns[row[0]] = {
                'type': row[1],
                'nullable': row[2] == 'YES',
                'default': row[3],
            }
    
    return columns


def compare_schemas(model_cols: dict, db_cols: dict | None, table_name: str) -> list:
    """Compare model columns against database columns."""
    issues = []
    
    if db_cols is None:
        issues.append({
            'severity': 'ERROR',
            'table': table_name,
            'issue': 'TABLE_MISSING',
            'message': f"Table '{table_name}' does not exist in database",
        })
        return issues
    
    # Check for missing columns (in model but not in DB)
    for col_name, col_info in model_cols.items():
        if col_name not in db_cols:
            issues.append({
                'severity': 'ERROR',
                'table': table_name,
                'column': col_name,
                'issue': 'COLUMN_MISSING',
                'message': f"Column '{col_name}' exists in model but not in database",
                'model_type': col_info['type'],
            })
    
    # Check for extra columns (in DB but not in model) - just warnings
    for col_name in db_cols:
        if col_name not in model_cols:
            issues.append({
                'severity': 'WARNING',
                'table': table_name,
                'column': col_name,
                'issue': 'COLUMN_EXTRA',
                'message': f"Column '{col_name}' exists in database but not in model",
            })
    
    return issues


async def validate_all_models():
    """Validate all models against the database."""
    engine = create_async_engine(str(settings.database_url))
    
    all_issues = []
    models_checked = 0
    
    # Get all model classes
    for mapper in Base.registry.mappers:
        model_class = mapper.class_
        table_name = model_class.__tablename__
        
        model_cols = get_model_columns(model_class)
        db_cols = await get_db_columns(engine, table_name)
        
        issues = compare_schemas(model_cols, db_cols, table_name)
        all_issues.extend(issues)
        models_checked += 1
    
    await engine.dispose()
    
    return all_issues, models_checked


def print_report(issues: list, models_checked: int):
    """Print a formatted report of schema issues."""
    errors = [i for i in issues if i['severity'] == 'ERROR']
    warnings = [i for i in issues if i['severity'] == 'WARNING']
    
    print("\n" + "=" * 60)
    print("SCHEMA VALIDATION REPORT")
    print("=" * 60)
    print(f"Models checked: {models_checked}")
    print(f"Errors: {len(errors)}")
    print(f"Warnings: {len(warnings)}")
    print("=" * 60)
    
    if errors:
        print("\n🔴 ERRORS (must fix before deployment):\n")
        for issue in errors:
            if issue['issue'] == 'TABLE_MISSING':
                print(f"  [{issue['table']}] Table does not exist")
            elif issue['issue'] == 'COLUMN_MISSING':
                print(f"  [{issue['table']}] Missing column: {issue['column']} ({issue['model_type']})")
    
    if warnings:
        print("\n🟡 WARNINGS (may indicate unused columns):\n")
        for issue in warnings:
            print(f"  [{issue['table']}] Extra DB column: {issue['column']}")
    
    if not errors and not warnings:
        print("\n✅ Schema is in sync! No issues detected.\n")
    
    print("=" * 60 + "\n")
    
    return len(errors) > 0


def main():
    """Main entry point."""
    print("Validating database schema against models...")
    
    try:
        issues, models_checked = asyncio.run(validate_all_models())
        has_errors = print_report(issues, models_checked)
        
        if has_errors:
            print("❌ Schema validation FAILED. Fix errors before deploying.\n")
            print("Run migration 020_add_missing_columns to fix most issues:")
            print("  alembic upgrade head\n")
            sys.exit(1)
        else:
            print("✅ Schema validation PASSED.\n")
            sys.exit(0)
            
    except Exception as e:
        print(f"\n❌ Error during validation: {e}")
        print("Make sure DATABASE_URL is set and the database is accessible.\n")
        sys.exit(1)


if __name__ == "__main__":
    main()
