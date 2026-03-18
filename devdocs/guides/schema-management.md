# Schema Management

Best practices for keeping SQLAlchemy models in sync with database migrations.

## The Problem

Schema drift happens when:
1. A developer adds a column to a model but forgets to create a migration
2. Migrations use different column names than the model expects
3. Type mismatches between model definitions and migration code

This causes runtime errors like:
```
sqlalchemy.exc.ProgrammingError: column workspaces.description does not exist
```

## Prevention

### 1. Schema Validation Script

Run before deployment:
```bash
python scripts/validate_schema.py
```

This compares all SQLAlchemy models against the actual database schema.

### 2. Git Pre-Push Hook

Install the hook to prevent pushing broken schemas:
```bash
cp scripts/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
```

### 3. Development Workflow

When adding a new column to a model:

1. **Add the column to the model file**
   ```python
   class MyModel(Base, TimestampMixin):
       new_field: Mapped[str | None] = mapped_column(String(100), nullable=True)
   ```

2. **Create a migration immediately**
   ```bash
   # Option A: Auto-generate (review carefully!)
   alembic revision --autogenerate -m "Add new_field to my_model"
   
   # Option B: Manual (preferred for complex changes)
   alembic revision -m "Add new_field to my_model"
   ```

3. **Verify the migration matches the model**
   - Column names must match exactly
   - Types should be equivalent
   - Nullable/default values must match

4. **Test locally**
   ```bash
   alembic upgrade head
   python scripts/validate_schema.py
   ```

### 4. Column Naming Convention

Always match these between model and migration:

| Model Field | Migration Column |
|-------------|------------------|
| `user_id` | `user_id` |
| `created_by` | `created_by` |
| `hashed_password` | `hashed_password` |

**Don't** use different names like:
- Model: `user_id` → Migration: `author_id` ❌
- Model: `hashed_password` → Migration: `password_hash` ❌

### 5. Using TimestampMixin

If your model uses `TimestampMixin`, ensure the migration creates:
```python
sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now())
sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now())
```

Not legacy names like `joined_at`.

## Recovery

If schema drift is detected in production:

1. **Create a fix migration** (like `020_add_missing_columns.py`)
   - Use `column_exists()` checks for idempotency
   - Handle column renames with `op.alter_column()`

2. **Deploy the fix**
   - Migrations run automatically via `entrypoint.sh`

3. **Validate**
   ```bash
   python scripts/validate_schema.py
   ```

## CI/CD Integration

Add to your GitHub Actions workflow:

```yaml
- name: Validate Schema
  run: |
    python scripts/validate_schema.py
  env:
    DATABASE_URL: ${{ secrets.TEST_DATABASE_URL }}
```

## Common Issues

### "column X does not exist"
- Missing migration for that column
- Fix: Create migration to add the column

### "column X has wrong type"
- Model type doesn't match migration type
- Fix: Create migration to alter column type

### "table X does not exist"
- Entire table migration is missing
- Fix: Create migration to create the table
