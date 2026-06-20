"""
The Damage Report — Visualization Suite
Exports 5 Plotly charts as HTML files from BigQuery mart tables.

Usage:
    python ingestion/visualize.py --project damagereport-499916 --keyfile path/to/key.json

Output: docs/charts/ directory with one HTML file per chart.
"""

import argparse
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import plotly.express as px
from google.cloud import bigquery
from google.oauth2 import service_account


# --- BigQuery helpers -------------------------------------------------

def get_client(project: str, keyfile: str) -> bigquery.Client:
    creds = service_account.Credentials.from_service_account_file(
        keyfile,
        scopes=["https://www.googleapis.com/auth/cloud-platform"],
    )
    return bigquery.Client(project=project, credentials=creds)


def query(client: bigquery.Client, sql: str) -> pd.DataFrame:
    return client.query(sql).result().to_dataframe()


# --- Chart 1: Event frequency over time ------------------------------

def chart_event_frequency(client: bigquery.Client, out_dir: Path) -> None:
    df = query(client, """
        SELECT event_year, SUM(event_count) as total_events
        FROM `damagereport-499916.dbt_marts.mart_severity_trends`
        GROUP BY event_year
        ORDER BY event_year
    """)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=df['event_year'], y=df['total_events'],
        mode='lines+markers',
        line=dict(color='#2196F3', width=2),
        marker=dict(size=4),
        name='Reported events'
    ))

    fig.update_layout(
        title=dict(text='Storm Events Are Being Reported More Frequently<br><sup>Annual reported event count, NOAA Storm Events Database 1996–2025</sup>', x=0.5),
        xaxis_title='Year',
        yaxis_title='Number of reported events',
        template='plotly_white',
        annotations=[dict(
            x=2006, y=df[df['event_year']==2006]['total_events'].values[0],
            text='Post-Katrina reporting<br>infrastructure expansion',
            showarrow=True, arrowhead=2, ax=60, ay=-40,
            font=dict(size=11)
        )]
    )

    fig.write_html(out_dir / 'chart1_event_frequency.html')
    print('Chart 1 saved: event frequency over time')


# --- Chart 2: Damage per event over time (Katrina annotated) ---------

def chart_damage_per_event(client: bigquery.Client, out_dir: Path) -> None:
    df = query(client, """
        SELECT
            event_year,
            ROUND(SUM(total_damage_2024_usd) / NULLIF(SUM(event_count), 0) / 1e6, 3)
                as avg_damage_per_event_millions
        FROM `damagereport-499916.dbt_marts.mart_severity_trends`
        GROUP BY event_year
        ORDER BY event_year
    """)

    # Separate Katrina year for highlighting
    katrina = df[df['event_year'] == 2005]
    non_katrina = df[df['event_year'] != 2005]

    fig = go.Figure()

    # Normal years
    fig.add_trace(go.Scatter(
        x=non_katrina['event_year'],
        y=non_katrina['avg_damage_per_event_millions'],
        mode='lines+markers',
        line=dict(color='#2196F3', width=2),
        marker=dict(size=4),
        name='Avg damage per event (2024 $M)'
    ))

    # Katrina spike — highlighted in red
    fig.add_trace(go.Scatter(
        x=katrina['event_year'],
        y=katrina['avg_damage_per_event_millions'],
        mode='markers',
        marker=dict(size=12, color='#F44336', symbol='star'),
        name='2005 (Hurricane Katrina)'
    ))

    katrina_val = katrina['avg_damage_per_event_millions'].values[0]
    fig.add_annotation(
        x=2005, y=katrina_val,
        text=f'<b>2005 — Hurricane Katrina</b><br>${katrina_val:.1f}M avg per event<br>(~4–10× a typical year)',
        showarrow=True, arrowhead=2, ax=-120, ay=-50,
        font=dict(size=11), bgcolor='white', bordercolor='#F44336', borderwidth=1
    )

    fig.update_layout(
        title=dict(text='Storm Severity Per Event Has Risen — With One Extreme Outlier<br><sup>Average damage per storm event in 2024 dollars, 1996–2025</sup>', x=0.5),
        xaxis_title='Year',
        yaxis_title='Avg damage per event ($M, 2024 dollars)',
        template='plotly_white',
        showlegend=True
    )

    fig.write_html(out_dir / 'chart2_damage_per_event.html')
    print('Chart 2 saved: damage per event (Katrina annotated)')


# --- Chart 3: Economic impact by event type --------------------------

def chart_economic_impact(client: bigquery.Client, out_dir: Path) -> None:
    df = query(client, """
        SELECT
            event_type_group,
            total_events,
            ROUND(avg_damage_per_event_2024_usd / 1e6, 2) as avg_damage_millions,
            ROUND(total_damage_2024_usd / 1e9, 2) as total_damage_billions,
            rank_by_damage_per_event
        FROM `damagereport-499916.dbt_marts.mart_economic_impact`
        ORDER BY rank_by_damage_per_event
    """)

    fig = go.Figure()

    fig.add_trace(go.Bar(
        x=df['avg_damage_millions'],
        y=df['event_type_group'],
        orientation='h',
        marker_color='#E53935',
        text=[f'${v:.1f}M' for v in df['avg_damage_millions']],
        textposition='outside',
        customdata=df[['total_events', 'total_damage_billions']].values,
        hovertemplate=(
            '<b>%{y}</b><br>'
            'Avg damage per event: $%{x:.2f}M<br>'
            'Total events: %{customdata[0]:,}<br>'
            'Total damage: $%{customdata[1]:.1f}B<br>'
            '<extra></extra>'
        )
    ))

    fig.update_layout(
        title=dict(text='Hurricanes Are the Most Destructive Per Occurrence<br><sup>Average property + crop damage per event in 2024 dollars</sup>', x=0.5),
        xaxis_title='Average damage per event ($M, 2024 dollars)',
        yaxis=dict(autorange='reversed'),
        template='plotly_white',
        margin=dict(l=120)
    )

    fig.write_html(out_dir / 'chart3_economic_impact.html')
    print('Chart 3 saved: economic impact by event type')


# --- Chart 4: Geographic risk choropleth by decade -------------------

def chart_geographic_risk(client: bigquery.Client, out_dir: Path) -> None:
    df = query(client, """
        SELECT state, decade,
               ROUND(total_damage_2024_usd / 1e9, 2) as total_damage_billions,
               total_deaths_direct, event_count
        FROM `damagereport-499916.dbt_marts.mart_geographic_risk`
        WHERE decade >= 1996
        ORDER BY decade, state
    """)

    # State name → abbreviation mapping for Plotly choropleth
    state_abbrev = {
        'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR',
        'California': 'CA', 'Colorado': 'CO', 'Connecticut': 'CT', 'Delaware': 'DE',
        'Florida': 'FL', 'Georgia': 'GA', 'Hawaii': 'HI', 'Idaho': 'ID',
        'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
        'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD',
        'Massachusetts': 'MA', 'Michigan': 'MI', 'Minnesota': 'MN',
        'Mississippi': 'MS', 'Missouri': 'MO', 'Montana': 'MT', 'Nebraska': 'NE',
        'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ',
        'New Mexico': 'NM', 'New York': 'NY', 'North Carolina': 'NC',
        'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK', 'Oregon': 'OR',
        'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC',
        'South Dakota': 'SD', 'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT',
        'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA',
        'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY'
    }

    df['state_abbrev'] = df['state'].map(state_abbrev)
    df = df[df['state_abbrev'].notna()]

    decades = sorted(df['decade'].unique())
    frames = []
    for decade in decades:
        decade_df = df[df['decade'] == decade]
        frames.append(go.Frame(
            data=[go.Choropleth(
                locations=decade_df['state_abbrev'],
                z=decade_df['total_damage_billions'],
                locationmode='USA-states',
                colorscale='Reds',
                zmin=0,
                zmax=df['total_damage_billions'].quantile(0.95),
                colorbar_title='Damage ($B)',
                customdata=decade_df[['state', 'total_deaths_direct', 'event_count']].values,
                hovertemplate=(
                    '<b>%{customdata[0]}</b><br>'
                    'Total damage: $%{z:.1f}B<br>'
                    'Deaths: %{customdata[1]:,}<br>'
                    'Events: %{customdata[2]:,}<br>'
                    '<extra></extra>'
                )
            )],
            name=str(decade),
            layout=go.Layout(title_text=f'Geographic Risk by Decade — {decade}s')
        ))

    first = df[df['decade'] == decades[0]]
    fig = go.Figure(
        data=[go.Choropleth(
            locations=first['state_abbrev'],
            z=first['total_damage_billions'],
            locationmode='USA-states',
            colorscale='Reds',
            zmin=0,
            zmax=df['total_damage_billions'].quantile(0.95),
            colorbar_title='Damage ($B, 2024 $)',
        )],
        layout=go.Layout(
            title=dict(text=f'Geographic Risk by Decade — {decades[0]}s<br><sup>Total damage in 2024 dollars. Use slider to move across decades.</sup>', x=0.5),
            geo=dict(scope='usa'),
            template='plotly_white',
            updatemenus=[dict(
                type='buttons', showactive=False,
                y=0, x=0.5, xanchor='center',
                buttons=[dict(label='▶ Play', method='animate',
                              args=[None, dict(frame=dict(duration=1500, redraw=True),
                                               fromcurrent=True)])]
            )],
            sliders=[dict(
                steps=[dict(method='animate', args=[[str(d)],
                            dict(mode='immediate', frame=dict(duration=1500, redraw=True))],
                            label=f'{d}s') for d in decades],
                currentvalue=dict(prefix='Decade: '),
                y=0.05
            )]
        ),
        frames=frames
    )

    fig.write_html(out_dir / 'chart4_geographic_risk.html')
    print('Chart 4 saved: geographic risk choropleth by decade')


# --- Chart 5: Surprise states ----------------------------------------

def chart_surprise_states(client: bigquery.Client, out_dir: Path) -> None:
    df = query(client, """
        SELECT state, decade,
               damage_percentile_in_decade,
               prior_decade_damage_percentile,
               ROUND(percentile_rank_change, 1) as rank_change,
               ROUND(total_damage_2024_usd / 1e9, 2) as damage_billions,
               is_surprise_state
        FROM `damagereport-499916.dbt_marts.mart_surprise_states`
        WHERE is_surprise_state = TRUE
        ORDER BY rank_change DESC
        LIMIT 20
    """)

    df['label'] = df['state'] + ' (' + df['decade'].astype(str) + 's)'

    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=df['rank_change'],
        y=df['label'],
        orientation='h',
        marker_color='#FF6F00',
        text=[f'+{v:.0f} pts' for v in df['rank_change']],
        textposition='outside',
        customdata=df[['damage_billions', 'prior_decade_damage_percentile',
                       'damage_percentile_in_decade']].values,
        hovertemplate=(
            '<b>%{y}</b><br>'
            'Percentile rank change: +%{x:.0f} points<br>'
            'Prior decade percentile: %{customdata[1]:.0f}<br>'
            'This decade percentile: %{customdata[2]:.0f}<br>'
            'Total damage: $%{customdata[0]:.1f}B<br>'
            '<extra></extra>'
        )
    ))

    fig.update_layout(
        title=dict(text='Surprise States — Biggest Decade-Over-Decade Risk Jumps<br><sup>States where damage percentile rank increased >20 points vs prior decade</sup>', x=0.5),
        xaxis_title='Percentile rank increase (points)',
        yaxis=dict(autorange='reversed'),
        template='plotly_white',
        margin=dict(l=200),
        height=600
    )

    fig.write_html(out_dir / 'chart5_surprise_states.html')
    print('Chart 5 saved: surprise states')


# --- Main ------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description='Generate Damage Report visualizations')
    parser.add_argument('--project', required=True)
    parser.add_argument('--keyfile', required=True)
    args = parser.parse_args()

    out_dir = Path('docs/charts')
    out_dir.mkdir(parents=True, exist_ok=True)

    client = get_client(args.project, args.keyfile)

    chart_event_frequency(client, out_dir)
    chart_damage_per_event(client, out_dir)
    chart_economic_impact(client, out_dir)
    chart_geographic_risk(client, out_dir)
    chart_surprise_states(client, out_dir)

    print(f'\nAll charts saved to {out_dir}/')
    print('Open any .html file in your browser to view.')


if __name__ == '__main__':
    main()
