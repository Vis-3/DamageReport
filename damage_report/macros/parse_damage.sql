{% macro parse_damage_string(column_name) %}
    CASE
        -- Empty string and NULL both mean "not recorded" — return NULL so
        -- downstream aggregates (SUM, AVG) ignore them rather than skewing results
        WHEN {{ column_name }} IS NULL THEN NULL
        WHEN TRIM({{ column_name }}) = '' THEN NULL

        -- K or T suffix: thousands (e.g. "10.00K" → 10000.0, "0T" → 0.0)
        -- NOAA used both K and T for thousands in different eras
        -- Guard: bare "K" or "T" with no numeric part → NULL (unparseable)
        WHEN UPPER({{ column_name }}) LIKE '%K'
            THEN SAFE_CAST(REPLACE(UPPER({{ column_name }}), 'K', '') AS FLOAT64) * 1000
        WHEN UPPER({{ column_name }}) LIKE '%T'
            THEN SAFE_CAST(REPLACE(UPPER({{ column_name }}), 'T', '') AS FLOAT64) * 1000

        -- M suffix: millions (e.g. "2.5M" → 2500000.0)
        WHEN UPPER({{ column_name }}) LIKE '%M'
            THEN SAFE_CAST(REPLACE(UPPER({{ column_name }}), 'M', '') AS FLOAT64) * 1000000

        -- B suffix: billions (e.g. "1.5B" → 1500000000.0)
        WHEN UPPER({{ column_name }}) LIKE '%B'
            THEN SAFE_CAST(REPLACE(UPPER({{ column_name }}), 'B', '') AS FLOAT64) * 1000000000

        -- No suffix: plain number, cast directly (e.g. "500" or "0")
        ELSE CAST({{ column_name }} AS FLOAT64)
    END
{% endmacro %}
