"""A richer Textual app: a scrollable, clickable document browser.

Demonstrates lots of text, scrolling, and mouse interaction over SSH:
- a sidebar list of articles (click, or use the arrow keys, to open one)
- a long Markdown pane on the right that scrolls (mouse wheel or PageUp/PageDown)

Serve it with ``examples/serve_docbrowser.py`` or preview locally with
``python examples/docbrowser.py``.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.containers import Horizontal, VerticalScroll
from textual.widgets import Footer, Header, Label, ListItem, ListView, Markdown

_LOREM = (
    "Terminal user interfaces have quietly become one of the most pleasant ways "
    "to ship software. They start fast, they run anywhere a shell runs, and they "
    "demand almost nothing of the machine on the other end. Over SSH this becomes "
    "something close to magic: the program lives on the server, and the client "
    "needs only a terminal.\n\n"
)


def _article(title: str, blurb: str, sections: list[str]) -> str:
    parts = [f"# {title}\n", f"*{blurb}*\n"]
    for i, section in enumerate(sections, 1):
        parts.append(f"## {i}. {section}\n")
        parts.append(_LOREM)
        parts.append(_LOREM)
        parts.append(
            "- point one worth remembering\n"
            "- a second point, slightly longer than the first\n"
            "- and a third for good measure\n"
        )
        parts.append("\n> A pull quote to break up the wall of text.\n")
        parts.append(_LOREM)
    parts.append("```python\nprint('the end of " + title + "')\n```\n")
    return "\n".join(parts)


# (title, markdown body) — enough text in each to require scrolling.
DOCS: list[tuple[str, str]] = [
    (
        "Getting Started",
        "How to think about serving TUIs over SSH.",
        ["Why terminals", "Why SSH", "A minimal server", "Where to go next"],
    ),
    (
        "Rendering",
        "How the screen actually gets drawn.",
        ["The driver", "Escape codes", "Repainting", "Performance"],
    ),
    (
        "Input",
        "Keys, mouse, resize, and paste.",
        ["The key parser", "Mouse events", "Resize handling", "Bracketed paste"],
    ),
    (
        "Concurrency",
        "Serving many sessions at once.",
        ["Isolation", "Subinterpreters", "Parallel rendering", "Limits"],
    ),
    (
        "Deployment",
        "Taking it to production.",
        ["Host keys", "Authentication", "Resource caps", "Monitoring"],
    ),
]


class DocBrowser(App):
    """A two-pane document browser: clickable list + scrollable reader."""

    CSS = """
    Screen { layout: vertical; }
    #body { height: 1fr; }
    #sidebar {
        width: 34;
        border-right: solid $accent;
        background: $panel;
    }
    #sidebar > ListView { height: 1fr; }
    #reader { padding: 0 2; }
    ListItem { padding: 0 1; }
    """

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("j", "next_doc", "Next"),
        ("k", "prev_doc", "Prev"),
    ]

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with VerticalScroll(id="sidebar"):
                yield ListView(
                    *(
                        ListItem(Label(title), id=f"doc-{i}")
                        for i, (title, _blurb, _sections) in enumerate(DOCS)
                    ),
                    id="docs",
                )
            with VerticalScroll(id="reader"):
                yield Markdown(id="md")
        yield Footer()

    def on_mount(self) -> None:
        self._show(0)
        self.query_one("#docs", ListView).focus()

    def _show(self, index: int) -> None:
        index = max(0, min(index, len(DOCS) - 1))
        self._index = index
        title, blurb, sections = DOCS[index]
        self.query_one("#md", Markdown).update(_article(title, blurb, sections))
        # Jump the reader back to the top when the document changes.
        self.query_one("#reader", VerticalScroll).scroll_home(animate=False)
        self.title = "textish docs"
        self.sub_title = title

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        item_id = event.item.id or "doc-0"
        self._show(int(item_id.removeprefix("doc-")))

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.item is not None and event.item.id:
            self._show(int(event.item.id.removeprefix("doc-")))

    def action_next_doc(self) -> None:
        self.query_one("#docs", ListView).action_cursor_down()

    def action_prev_doc(self) -> None:
        self.query_one("#docs", ListView).action_cursor_up()


if __name__ == "__main__":
    DocBrowser().run()
