import dash
from dash import html, dcc

dash.register_page(__name__, path="/", name="Portfolio")


def layout():
    return html.Div([
        html.H1("Portfolio"),
        html.P("Current weights, tilts, and positions — placeholder."),
    ])
