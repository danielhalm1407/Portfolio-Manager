import dash
from dash import html

dash.register_page(__name__, path="/conviction", name="Conviction")


def layout():
    return html.Div([
        html.H1("Conviction"),
        html.P("Deep-dive fundamental write-ups per name/sector — placeholder."),
    ])
