"""
Unified logging subsystem for limitlens.

Call ``get_logger()`` to obtain the shared ``logging.Logger`` instance.
All records are written through a ``RedactFilter`` that strips emails,
home-directory paths, and bearer/cookie tokens before they reach the log
file, preventing accidental privacy leaks.

Environment variables
---------------------
LIMITLENS_LOG_PATH
    Override the default log file location (``~/.cache/limitlens/limitlens.log``).
LIMITLENS_LOG_LEVEL
    Python logging level name (``DEBUG``, ``INFO``, ``WARNING``, ...).
    Defaults to ``WARNING`` so routine runs are silent.
"""

import logging
import os
import threading
from logging.handlers import RotatingFileHandler

_LOGGER = None
_HANDLER = None
_CONFIGURED = False
_CONFIG_LOCK = threading.Lock()

# Home dir cached at import time for speed
_HOME = os.path.expanduser("~")
_TOKEN_RE = None  # compiled lazily to avoid import-time regex cost


def _compile_token_re():
    """Return a compiled pattern that matches common auth tokens in log text."""
    import re
    return re.compile(
        r"(?:"
        r"sso=[A-Za-z0-9._\-]{8,}"
        r"|sso-rw=[A-Za-z0-9._\-]{8,}"
        r"|Authorization:\s*Bearer\s+\S+"
        r"|Bearer\s+[A-Za-z0-9._\-]{16,}"
        r"|cookie:\s*\S+"
        r"|api[_-]?key[=:]\s*\S+"
        r")"
    )


class RedactFilter(logging.Filter):
    """Strip PII from every log record's msg and args before formatting.

    exc_text is NOT available at filter time (it's populated by the Formatter).
    Traceback redaction is handled in ``RedactingHandler.emit()`` instead.
    """

    def filter(self, record):  # noqa: A003
        if isinstance(record.msg, str):
            record.msg = _redact(record.msg)
        if record.args:
            if isinstance(record.args, tuple):
                record.args = tuple(
                    _redact(a) if isinstance(a, str) else a
                    for a in record.args
                )
            elif isinstance(record.args, dict):
                record.args = {
                    k: (_redact(v) if isinstance(v, str) else v)
                    for k, v in record.args.items()
                }
        return True


class RedactingHandler(RotatingFileHandler):
    """RotatingFileHandler that redacts the *fully-formatted* log line.

    Python's logging pipeline is: Filter → Formatter → Handler.emit().
    ``record.exc_text`` is only populated *inside* ``Formatter.format()``.
    By overriding ``emit()``, we can redact after formatting (including the
    formatted traceback) and before the bytes hit the disk.
    """

    def emit(self, record):
        try:
            # Let the formatter populate record.exc_text / record.message.
            msg = self.format(record)
            # Redact the fully formatted string (includes traceback).
            msg = _redact(msg)
            # Use the standard StreamHandler write path for thread safety.
            self.acquire()
            try:
                self._ensure_stream()  # type: ignore[attr-defined]
                stream = self.stream
                stream.write(msg + self.terminator)
                self.flush()
                if self.shouldRollover(record):
                    self.doRollover()
            finally:
                self.release()
        except Exception:  # pragma: no cover
            self.handleError(record)

    def _ensure_stream(self):
        """Open the stream if it has been closed (e.g. after rollover)."""
        if self.stream is None:
            self.stream = self._open()  # type: ignore[attr-defined]


def _redact_email(email):
    """Mask an email address, keeping the domain visible."""
    if "@" not in email:
        return email
    user, domain = email.split("@", 1)
    if len(user) > 2:
        return f"{user[:2]}***@{domain}"
    return f"***@{domain}"


def _redact(text):
    """Apply all redaction rules to *text* and return the scrubbed string."""
    import re
    if not text:
        return text
    # Redact email addresses
    text = re.sub(
        r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b",
        lambda m: _redact_email(m.group(0)),
        text,
    )
    # Redact home directory paths
    if _HOME and _HOME != "/":
        text = text.replace(_HOME, "~")
    # Redact .codex account paths
    text = re.sub(r'(\.codex-)[^/\s]+', r'\1***', text)
    # Redact auth tokens / cookies
    global _TOKEN_RE
    if _TOKEN_RE is None:
        _TOKEN_RE = _compile_token_re()
    text = _TOKEN_RE.sub("[REDACTED]", text)
    return text


def get_logger(name="limitlens"):
    """Return a limitlens logger, initialising the shared handler on first call.

    A single ``RedactingHandler`` is attached to the root ``"limitlens"``
    logger.  Child loggers (e.g. ``"limitlens.providers.grok"``) propagate
    to the parent and must **not** receive their own handler, so that only
    one rotating file writer ever targets the log file.  This prevents
    rollover races that occur when multiple independent handlers attempt
    to roll the same file concurrently.

    Set ``LIMITLENS_LOG_LEVEL=DEBUG`` for verbose output.
    """
    global _LOGGER, _HANDLER, _CONFIGURED

    if name != "limitlens" and not name.startswith("limitlens."):
        raise ValueError("logger name must be 'limitlens' or start with 'limitlens.'")

    logger = logging.getLogger(name)

    # Always configure the root "limitlens" logger, never child loggers.
    root_logger = logging.getLogger("limitlens")

    if not _CONFIGURED:
        with _CONFIG_LOCK:
            if not _CONFIGURED:
                level_name = os.environ.get("LIMITLENS_LOG_LEVEL", "WARNING").upper()
                level = getattr(logging, level_name, logging.WARNING)
                root_logger.setLevel(level)
                root_logger.propagate = False

                log_path = os.environ.get("LIMITLENS_LOG_PATH") or os.path.join(
                    os.path.expanduser("~/.cache/limitlens"), "limitlens.log"
                )

                try:
                    log_dir = os.path.dirname(log_path)
                    if not os.path.isdir(log_dir):
                        os.makedirs(log_dir, mode=0o700, exist_ok=True)
                    else:
                        os.chmod(log_dir, 0o700)
                    # Use RedactingHandler so tracebacks are redacted after formatting.
                    handler = RedactingHandler(
                        log_path,
                        maxBytes=1_000_000,
                        backupCount=3,
                        encoding="utf-8",
                    )
                    handler.addFilter(RedactFilter())
                    try:
                        os.chmod(log_path, 0o600)
                    except OSError:
                        pass
                    handler.setFormatter(
                        logging.Formatter(
                            "[%(asctime)s] %(levelname)s %(name)s: %(message)s",
                            datefmt="%Y-%m-%dT%H:%M:%S",
                        )
                    )
                    root_logger.addHandler(handler)
                    _HANDLER = handler
                except OSError:
                    # Fallback: no file logging (e.g. in sandboxed test environments).
                    root_logger.addHandler(logging.NullHandler())
                _CONFIGURED = True

    _LOGGER = root_logger
    return logger
