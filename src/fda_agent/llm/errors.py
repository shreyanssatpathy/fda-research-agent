"""Translate Anthropic SDK errors into project errors the UI can render.

Without this a transient 529 propagates out of `research()` and kills the whole
Streamlit script with a traceback — an outage on Anthropic's side presented to the
user as a crash in this application.

The distinction that matters is **retryable or not**, because it decides what the
user should be told to do. A 529 means wait and try again; a 400 means the request
was wrong and retrying will not help.
"""
from __future__ import annotations

from contextlib import contextmanager


class LLMUnavailable(Exception):
    """The model could not be reached. Carries whether retrying is worthwhile."""

    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None):
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


@contextmanager
def translate_api_errors():
    """Wrap a model call, converting SDK exceptions into `LLMUnavailable`.

    Ordered most-specific first. The SDK has already retried the retryable cases
    (it retries 408, 409, 429 and any 5xx) before anything reaches here, so an
    exception arriving means retrying in-process has already failed.
    """
    import anthropic

    try:
        yield
    except anthropic.APIStatusError as err:
        code = err.status_code
        if code == 529:
            raise LLMUnavailable(
                "Anthropic's API is temporarily overloaded (529). This is on their "
                "side, not a problem with the question. The request was already "
                "retried automatically — wait a few seconds and ask again.",
                retryable=True, status_code=code,
            ) from err
        if code == 429:
            retry_after = err.response.headers.get("retry-after", "a few")
            raise LLMUnavailable(
                f"Rate limited (429). Retry after {retry_after} seconds.",
                retryable=True, status_code=code,
            ) from err
        if code >= 500:
            raise LLMUnavailable(
                f"Anthropic server error ({code}). Transient — try again shortly.",
                retryable=True, status_code=code,
            ) from err
        if code in (401, 403):
            raise LLMUnavailable(
                f"Authentication failed ({code}). Check ANTHROPIC_API_KEY and, for "
                "an identity-linked key, ANTHROPIC_WORKSPACE_ID.",
                retryable=False, status_code=code,
            ) from err
        raise LLMUnavailable(
            f"The API rejected the request ({code}): {err.message}. Retrying will "
            "not help.",
            retryable=False, status_code=code,
        ) from err
    except anthropic.APITimeoutError as err:
        raise LLMUnavailable(
            "The model did not respond in time. Try again.", retryable=True
        ) from err
    except anthropic.APIConnectionError as err:
        raise LLMUnavailable(
            "Could not reach the Anthropic API. Check network connectivity.",
            retryable=True,
        ) from err
