{% macro generate_schema_name(custom_schema_name, node) -%}
    {#
        Override dbt's default behavior of concatenating profile dataset + custom schema.

        Default: "dbt_staging" + "staging" → "dbt_staging_staging" (wrong)
        Ours:    use custom_schema_name as the full dataset name when provided.

        Result:
          +schema: staging  → dbt_staging  (staging + intermediate models)
          +schema: marts    → dbt_marts    (mart models)
          no schema config  → dbt_staging  (profile default)
    #}
    {%- if custom_schema_name is none -%}
        {{ target.schema }}
    {%- else -%}
        {{ custom_schema_name }}
    {%- endif -%}
{%- endmacro %}
