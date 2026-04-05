"""Reusable Plotly chart components shared across pages."""
import plotly.graph_objects as go


def weights_bar(labels: list[str], values: list[float]) -> go.Figure:
    """Horizontal bar chart for portfolio weights."""
    fig = go.Figure(go.Bar(
        x=values,
        y=labels,
        orientation="h",
    ))
    fig.update_layout(xaxis_title="Weight", yaxis_title="Asset")
    return fig
