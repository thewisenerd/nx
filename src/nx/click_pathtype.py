import os
from typing import Any, cast

from click import Context, Parameter, ParamType
from click.shell_completion import CompletionItem


class PathType(ParamType):
    name = "path"
    allowed_extensions: set[str] | None

    def __init__(self, /, allowed_extensions: set[str] | None = None) -> None:
        self.allowed_extensions = allowed_extensions

    def convert(
        self, value: Any, param: Parameter | None, ctx: Context | None
    ) -> str | None:
        if not value:
            # almost never would a user want "" to mean "."
            return None

        return cast(str, value)

    def shell_complete(
        self, ctx: Context, param: Parameter, incomplete: str
    ) -> list[CompletionItem]:
        full_incomplete = os.path.expanduser(incomplete)
        base_incomplete = os.path.basename(incomplete)

        dirname = os.path.dirname(incomplete)
        full_dirname = os.path.dirname(full_incomplete)

        entries = os.listdir(full_dirname) if full_dirname else os.listdir()

        if base_incomplete == "":
            entries = [e for e in entries if not e.startswith(".")]

        results: list[CompletionItem] = []
        for entry in entries:
            if entry.lower().startswith(base_incomplete.lower()):
                full_item = os.path.join(full_dirname, entry)
                item = os.path.join(dirname, entry)

                if os.path.isdir(full_item):
                    item += "/"
                else:
                    _, ext = os.path.splitext(entry)
                    if (
                        self.allowed_extensions is not None
                        and ext.lower() not in self.allowed_extensions
                    ):
                        continue

                results.append(CompletionItem(item))

        return results
