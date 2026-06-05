"""Render the admin Stage-3 aggregation panel body from an AggregationView.

Pure component construction (no callbacks). The per-decision inputs carry
pattern-matching IDs so a single callback can collect them:

* robustness final  : ``{"type": "agg-rob-final",  "cid": <int>}``  (dropdown)
* robustness reason : ``{"type": "agg-rob-reason", "cid": <int>}``  (input)
* edit accept       : ``{"type": "agg-edit-accept","key": <str>}``  (checklist)
* edit reason       : ``{"type": "agg-edit-reason","key": <str>}``  (input)
"""

from __future__ import annotations

from dash import dcc, html

from ..recommendations.aggregate import AggregationView

_TH = {"textAlign": "left", "padding": "0.25em 0.6em", "borderBottom": "2px solid #ccc",
       "fontSize": "0.8em", "color": "#444", "whiteSpace": "nowrap"}
_TD = {"padding": "0.2em 0.6em", "borderBottom": "1px solid #eee",
       "fontSize": "0.82em", "verticalAlign": "middle"}
_REASON_STYLE = {"width": "100%", "fontSize": "0.8em", "padding": "0.15em 0.35em",
                 "boxSizing": "border-box"}


def _flag(v: bool) -> str:
    return "Robust" if v else "Non-robust"


def _robustness_table(view: AggregationView) -> html.Table:
    head = (
        [html.Th("Cluster", style=_TH), html.Th("Current", style=_TH)]
        + [html.Th(rv, style=_TH) for rv in view.reviewers]
        + [html.Th("Final", style={**_TH, "minWidth": "120px"}),
           html.Th("Reason (optional)", style=_TH)]
    )
    rows = []
    for r in view.robustness_rows:
        vote_cells = []
        for rv in view.reviewers:
            v = r.votes.get(rv)
            txt = "·" if v is None else _flag(v)
            color = "#999" if v is None else ("#0a6" if v else "#c0392b")
            vote_cells.append(html.Td(txt, style={**_TD, "color": color}))
        default = "robust" if r.default_final else "non-robust"
        rows.append(html.Tr(
            [
                html.Td(str(r.cid), style={**_TD, "fontWeight": 600}),
                html.Td(_flag(r.current_robust), style=_TD),
            ]
            + vote_cells
            + [
                html.Td(dcc.Dropdown(
                    id={"type": "agg-rob-final", "cid": r.cid},
                    options=[{"label": "—", "value": ""},
                             {"label": "Robust", "value": "robust"},
                             {"label": "Non-robust", "value": "non-robust"}],
                    value=default, clearable=False,
                    style={"fontSize": "0.8em", "minWidth": "120px"},
                ), style=_TD),
                html.Td(dcc.Input(
                    id={"type": "agg-rob-reason", "cid": r.cid},
                    type="text", value="", placeholder="why…",
                    style=_REASON_STYLE,
                ), style={**_TD, "minWidth": "180px"}),
            ]
        ))
    return html.Table([html.Thead(html.Tr(head)), html.Tbody(rows)],
                      style={"borderCollapse": "collapse", "width": "100%"})


def _edits_table(view: AggregationView) -> html.Table:
    head = [html.Th("Suggested edit", style=_TH), html.Th("Proposed by", style=_TH),
            html.Th("Accept", style=_TH), html.Th("Reason (optional)", style=_TH)]
    rows = []
    for e in view.edit_rows:
        rows.append(html.Tr([
            html.Td(e.description, style={**_TD, "fontFamily": "ui-monospace, monospace"}),
            html.Td(", ".join(e.proposers), style={**_TD, "color": "#555"}),
            html.Td(dcc.Checklist(
                id={"type": "agg-edit-accept", "key": e.key},
                options=[{"label": "", "value": "y"}], value=[],
                style={"fontSize": "0.9em"},
            ), style={**_TD, "textAlign": "center"}),
            html.Td(dcc.Input(
                id={"type": "agg-edit-reason", "key": e.key},
                type="text", value="", placeholder="why…",
                style=_REASON_STYLE,
            ), style={**_TD, "minWidth": "180px"}),
        ]))
    return html.Table([html.Thead(html.Tr(head)), html.Tbody(rows)],
                      style={"borderCollapse": "collapse", "width": "100%"})


def _comments_block(view: AggregationView) -> html.Div:
    items = []
    for c in view.comments:
        bits = []
        if c.signs_off_robustness:
            bits.append(html.Div("• signs off on robustness as-is",
                                 style={"color": "#777"}))
        if c.source_comment:
            bits.append(html.Div([html.B("source: "), c.source_comment]))
        for cid, cm in c.cluster_comments:
            bits.append(html.Div([html.B(f"cl {cid}: "), cm]))
        for ep, cm in c.epoch_comments:
            bits.append(html.Div([html.B(f"{ep}: "), cm]))
        items.append(html.Details([
            html.Summary(c.reviewer, style={"cursor": "pointer", "fontWeight": 600}),
            html.Div(bits, style={"padding": "0.2em 0 0.4em 1em",
                                  "fontSize": "0.82em", "lineHeight": "1.4"}),
        ], open=False))
    if not items:
        return html.Div()
    return html.Div([
        html.Div("Reviewer comments (context)",
                 style={"fontWeight": 600, "fontSize": "0.85em",
                        "margin": "0.6em 0 0.2em", "color": "#444"}),
        *items,
    ])


def _section_title(text: str) -> html.Div:
    return html.Div(text, style={"fontWeight": 600, "fontSize": "0.9em",
                                 "margin": "0.8em 0 0.3em", "color": "#333"})


def build_aggregation_children(view: AggregationView) -> list:
    """The dynamic body of the aggregation panel for one source."""
    if view.is_empty():
        return [html.Div("No reviewer submissions for this source yet.",
                         style={"color": "#777", "fontStyle": "italic",
                                "padding": "0.3em 0"})]
    who = ", ".join(f"{rv} ({when})" for rv, when in view.submissions)
    head = html.Div(
        [html.B(f"{len(view.submissions)} submission"
                f"{'' if len(view.submissions) == 1 else 's'}: "), who],
        style={"fontSize": "0.85em", "marginBottom": "0.3em", "color": "#333"},
    )
    children: list = [head]

    if view.robustness_rows:
        children.append(_section_title("Robustness decisions (Final = majority of "
                                       "reviewer votes + current; ties → current)"))
        children.append(_robustness_table(view))
    else:
        children.append(html.Div("No robustness suggestions.",
                                 style={"color": "#888", "fontSize": "0.82em",
                                        "margin": "0.3em 0"}))

    if view.edit_rows:
        children.append(_section_title("Cross-ID / use-in-fit edits "
                                       "(check to accept)"))
        children.append(_edits_table(view))
    else:
        children.append(html.Div("No cross-ID / use-in-fit edits suggested.",
                                 style={"color": "#888", "fontSize": "0.82em",
                                        "margin": "0.3em 0"}))

    children.append(_comments_block(view))
    return children
