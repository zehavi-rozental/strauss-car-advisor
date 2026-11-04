-- Per table: row count and an md5 of every row's text, in a stable order. Read-only; hashes only, never data.
SELECT table_name,
       (xpath('/row/c/text()', query_to_xml(format('select count(*) as c from %I.%I', table_schema, table_name),
                                            false, true, '')))[1]::text AS row_count,
       (xpath('/row/f/text()', query_to_xml(format('select md5(coalesce(string_agg(t::text, %L order by t::text collate "C"), %L)) as f from %I.%I t',
                                                   '|', '', table_schema, table_name), false, true, '')))[1]::text AS content_md5
FROM information_schema.tables
WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
ORDER BY 1;
