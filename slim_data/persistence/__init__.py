"""Persistence: SQLAlchemy rows, repositories, Alembic migrations, append-only triggers.

Every connection goes through persistence.db.make_engine, which applies the OSS guard.
"""
