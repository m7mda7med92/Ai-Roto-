"""FAKE ``maya.utils``."""
from typing import Any, Callable


def executeInMainThreadWithResult(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:  # noqa: N802
    return fn(*args, **kwargs)


def executeDeferred(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> None:  # noqa: N802
    fn(*args, **kwargs)
