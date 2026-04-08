import random
from textual.app import App, ComposeResult
from textual.widgets import Header, Footer, Static, Input, Label
from textual.containers import Vertical, Horizontal, Center
from textual.reactive import reactive

WORDS = [
    "crane",
    "slate",
    "audio",
    "chess",
    "flame",
    "brave",
    "grind",
    "piano",
    "shard",
    "light",
    "ghost",
    "plumb",
    "frost",
    "quirk",
    "oxide",
]

MAX_GUESSES = 6
WORD_LENGTH = 5
MIN_COLS = 44
MIN_ROWS = 28


def score_guess(guess: str, target: str) -> list[str]:
    result = ["absent"] * WORD_LENGTH
    target_counts: dict[str, int] = {}
    for i, (g, t) in enumerate(zip(guess, target)):
        if g == t:
            result[i] = "correct"
        else:
            target_counts[t] = target_counts.get(t, 0) + 1
    for i, (g, t) in enumerate(zip(guess, target)):
        if result[i] != "correct" and g in target_counts and target_counts[g] > 0:
            result[i] = "present"
            target_counts[g] -= 1
    return result


class WordleApp(App):
    CSS = """
    #too-small {
        display: none;
        align: center middle;
        height: 1fr;
    }
    #too-small Label {
        text-align: center;
        color: $warning;
        text-style: bold;
    }
    #game {
        align: center middle;
        height: 1fr;
    }
    .board-row {
        height: 3;
        width: 39;
        align: center middle;
        margin-bottom: 1;
    }
    .cell {
        width: 7;
        height: 3;
        border: solid $surface-lighten-2;
        content-align: center middle;
        text-align: center;
        text-style: bold;
    }
    .cell.correct { background: #538d4e; border: solid #538d4e; color: white; }
    .cell.present { background: #b59f3b; border: solid #b59f3b; color: white; }
    .cell.absent  { background: #3a3a3c; border: solid #3a3a3c; color: white; }
    #message {
        margin-top: 1;
        margin-bottom: 1;
        text-align: center;
        width: 39;
        color: $text-muted;
    }
    #input { width: 39; }
    """

    BINDINGS = [("ctrl+c", "quit", "Quit")]

    guesses: reactive[list[tuple[str, list[str]]]] = reactive([])

    def __init__(self) -> None:
        super().__init__()
        self._target = random.choice(WORDS)
        self._game_over = False

    def compose(self) -> ComposeResult:
        yield Header()
        with Center(id="too-small"):
            yield Label(
                "Terminal too small.\nPlease resize to at least\n44 cols × 28 rows."
            )
        with Vertical(id="game"):
            for row in range(MAX_GUESSES):
                with Horizontal(classes="board-row"):
                    for col in range(WORD_LENGTH):
                        yield Static("", classes="cell", id=f"cell-{row}-{col}")
            yield Label("Guess a 5-letter word!", id="message")
            yield Input(placeholder="Type guess + Enter", id="input", max_length=5)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#input", Input).focus()
        self._check_size()

    def on_resize(self) -> None:
        self._check_size()

    def _check_size(self) -> None:
        too_small = self.size.width < MIN_COLS or self.size.height < MIN_ROWS
        self.query_one("#too-small").display = too_small
        self.query_one("#game").display = not too_small

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if self._game_over:
            return
        guess = event.value.strip().lower()
        self.query_one("#input", Input).clear()

        if len(guess) != WORD_LENGTH:
            self._set_message(f"Must be {WORD_LENGTH} letters.")
            return
        if not guess.isalpha():
            self._set_message("Letters only.")
            return

        scores = score_guess(guess, self._target)
        row = len(self.guesses)
        for col, (letter, score) in enumerate(zip(guess, scores)):
            cell = self.query_one(f"#cell-{row}-{col}", Static)
            cell.update(letter.upper())
            cell.set_classes(f"cell {score}")

        self.guesses = self.guesses + [(guess, scores)]

        if guess == self._target:
            self._set_message(f"You got it in {len(self.guesses)}! Ctrl+C to quit.")
            self._game_over = True
        elif len(self.guesses) == MAX_GUESSES:
            self._set_message(f"Out of guesses! Word: {self._target.upper()}")
            self._game_over = True
        else:
            remaining = MAX_GUESSES - len(self.guesses)
            self._set_message(
                f"{remaining} guess{'es' if remaining != 1 else ''} left."
            )

    def _set_message(self, text: str) -> None:
        self.query_one("#message", Label).update(text)


if __name__ == "__main__":
    WordleApp().run()
