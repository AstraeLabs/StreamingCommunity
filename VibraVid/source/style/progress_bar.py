# 04.01.25

from rich.progress import ProgressColumn
from rich.text import Text

from VibraVid.utils import internet_manager


class CustomBarColumn(ProgressColumn):
    def __init__(self, bar_width=40, complete_char="█", incomplete_char="░", complete_style="bright_magenta", incomplete_style="dim white",):
        super().__init__()
        self.bar_width = bar_width
        self.complete_char = complete_char
        self.incomplete_char = incomplete_char
        self.complete_style = complete_style
        self.incomplete_style = incomplete_style

    def render(self, task):
        completed = task.completed
        total = task.total or 100

        bar_width = int((completed / total) * self.bar_width) if total > 0 else 0
        bar_width = min(bar_width, self.bar_width)

        text = Text()
        if bar_width > 0:
            text.append(self.complete_char * bar_width, style=self.complete_style)
        if bar_width < self.bar_width:
            text.append(self.incomplete_char * (self.bar_width - bar_width), style=self.incomplete_style)

        return text


class CompactTimeColumn(ProgressColumn):
    def __init__(self, compact: bool = True):
        super().__init__()
        self.compact = compact

    def render(self, task):
        elapsed = task.finished_time if task.finished else task.elapsed
        if elapsed is None:
            return "[yellow]--:--[/yellow]"

        return f"[yellow]{internet_manager.format_time(elapsed)}[/yellow]"


class CompactTimeRemainingColumn(ProgressColumn):
    def render(self, task):
        remaining = task.time_remaining
        if remaining is None:
            return "[cyan]--:--[/cyan]"

        return f"[cyan]{internet_manager.format_time(remaining)}[/cyan]"


class ColoredSegmentColumn(ProgressColumn):
    def render(self, task):
        segment = task.fields.get("segment", "0/0")
        if "/" in segment:
            current, total = segment.split("/")
            return f"[green]{current}[/green][dim]/[/dim][cyan]{total}[/cyan]"
        return f"[yellow]{segment}[/yellow]"


class SizeColumn(ProgressColumn):
    def render(self, task):
        size = task.fields.get("size", "0B/0B")
        if "/" in size:
            current, total = size.split("/")
            return f"[dim]{current}/[/dim][green]{total}[/green]"
        return f"[green]{size}[/green]"