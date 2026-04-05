import dash
from dash import html, dcc

app = dash.Dash(
    __name__,
    use_pages=True,
    suppress_callback_exceptions=True,
)
server = app.server  # exposes the Flask server — required for Render deployment

app.layout = html.Div([
    dcc.Location(id="url"),
    html.Nav([
        dcc.Link("Portfolio", href="/"),
        dcc.Link("Themes", href="/themes"),
        dcc.Link("Conviction", href="/conviction"),
    ]),
    dash.page_container,
])

if __name__ == "__main__":
    app.run(debug=True)  # local only — production uses gunicorn
