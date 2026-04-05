import dash
from dash import html

dash.register_page(__name__, path="/themes", name="Themes")


def layout():
    return html.Div([
        html.H1("Themes"),
        html.P("Scored theme briefs — placeholder."),
    ])
