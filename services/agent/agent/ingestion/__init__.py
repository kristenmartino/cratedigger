"""Taste-profile ingestion.

Per Option C (decided 2026-05-08): the seed script must invoke this code path
rather than write hardcoded `INSERT INTO taste_profiles` rows. Same code path
that v1.1's quiz / playlist / history flows will hit.
"""
