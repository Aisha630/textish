from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Input, Label, Button, Log
from textual.containers import Vertical


class ChatApp(App):
    """A simple chat-style app that echoes messages back to the user."""

    CSS = """
    Vertical {
        padding: 1 2;
        height: 1fr;
    }
    Label {
        margin-bottom: 1;
        color: $text-muted;
    }
    Log {
        height: 1fr;
        border: solid $primary;
        margin-bottom: 1;
    }
    Input {
        margin-bottom: 1;
    }
    """

    def compose(self) -> ComposeResult:
        yield Header()
        with Vertical():
            yield Label("Type a message and press Enter:")
            yield Log(id="log")
            yield Input(placeholder="Say something...", id="message-input")
            yield Button("Send", id="send-btn", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#message-input", Input).focus()
        self.query_one(Log).write_line("Connected. Say hello!")

    def on_button_pressed(self) -> None:
        self._send()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._send()

    def _send(self) -> None:
        input_widget = self.query_one("#message-input", Input)
        message = input_widget.value.strip()
        if not message:
            return
        log = self.query_one(Log)
        log.write_line(f"You: {message}")
        log.write_line(f"Echo: {message[::-1]}")  # reverse the message as a reply
        input_widget.clear()


if __name__ == "__main__":
    ChatApp().run()
