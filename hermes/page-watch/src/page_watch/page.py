import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

from page_watch.errors import FetchError, InvalidInputError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
)
_MAX_BYTES = 3_000_000
_INVISIBLE = frozenset({"script", "style", "noscript", "template", "svg"})
_BLOCKS = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "button", "dd", "div", "dl", "dt",
        "fieldset", "figcaption", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "li", "main", "nav", "ol", "option", "p", "section", "table", "td", "th", "title",
        "tr", "ul",
    }
)  # fmt: skip
# Visible text of the interstitials that answer scripts instead of the page.
_ANTI_BOT_MARKERS = (
    "just a moment",
    "attention required",
    "verify you are human",
    "are you a robot",
    "captcha",
    "queue-it",
    "you are now in line",
    "access denied",
    "enable javascript and cookies",
)
_ANTI_BOT_MAX_LINES = 40


class _VisibleText(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.lines: list[str] = []
        self._current: list[str] = []
        self._hidden_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _INVISIBLE:
            self._hidden_depth += 1
        elif tag in _BLOCKS:
            self._break()

    def handle_endtag(self, tag: str) -> None:
        if tag in _INVISIBLE:
            self._hidden_depth = max(0, self._hidden_depth - 1)
        elif tag in _BLOCKS:
            self._break()

    def handle_data(self, data: str) -> None:
        if not self._hidden_depth:
            self._current.append(data)

    def close(self) -> None:
        super().close()
        self._break()

    def _break(self) -> None:
        line = " ".join(" ".join(self._current).split())
        if line:
            self.lines.append(line)
        self._current = []


def visible_lines(html: str) -> list[str]:
    parser = _VisibleText()
    parser.feed(html)
    parser.close()
    return parser.lines


def normalize_url(text: str) -> str:
    url = text.strip()
    if url.startswith("www."):
        url = f"https://{url}"
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("https", "http") or not parts.hostname:
        raise InvalidInputError(f"not a web page link: {text!r}")
    return url


def _looks_like_anti_bot(lines: list[str]) -> bool:
    if len(lines) > _ANTI_BOT_MAX_LINES:
        return False
    text = " ".join(lines).casefold()
    return any(marker in text for marker in _ANTI_BOT_MARKERS)


class PageFetcher:
    def __init__(self, timeout_seconds: float = 20) -> None:
        self._timeout = timeout_seconds

    def visible_lines(self, url: str) -> list[str]:
        """The page's text as a browser shows it without JavaScript, one line per block."""
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en,nl;q=0.8,es;q=0.6",
            },
        )
        host = urllib.parse.urlsplit(url).hostname
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                charset = response.headers.get_content_charset() or "utf-8"
                raw = response.read(_MAX_BYTES)
        except urllib.error.HTTPError as error:
            body = error.read(_MAX_BYTES).decode("utf-8", "replace")
            if error.code in (403, 429) or _looks_like_anti_bot(visible_lines(body)):
                raise FetchError(f"HTTP {error.code} from {host}: the site blocks scripts") from None
            raise FetchError(f"HTTP {error.code} from {host}") from None
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            reason = getattr(error, "reason", error)
            raise FetchError(f"cannot reach {host}: {reason}") from None
        lines = visible_lines(raw.decode(charset, "replace"))
        if _looks_like_anti_bot(lines):
            raise FetchError(f"{host} answered with an anti-bot page (Cloudflare, captcha or a queue)")
        return lines
