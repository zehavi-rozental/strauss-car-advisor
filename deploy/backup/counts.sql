-- Row count per table + the schema version. SELECT only; the session is also read-only on the server.
SELECT table_name,
       (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name),
                                            false, true, '')))[1]::text::bigint AS row_count
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY 1;
-- its own key: a table is also called alembic_version
SELECT 'schema_version', version_num FROM alembic_version;
