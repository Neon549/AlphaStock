# Document RAG

Use this skill only to read documents already attached to the current session.

1. Retrieve by section path and child chunk from PostgreSQL + pgvector.
2. Include adjacent chunks only when needed to complete local context.
3. Return source file, section path and page citations with every evidence item.
4. Do not fabricate a document claim when retrieval returns no evidence.
5. This skill has read-only `document:read` permission and must never mutate or delete files.
