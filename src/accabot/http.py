from __future__ import annotations

import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class ApiError(RuntimeError):
    pass


def get_json(
    url: str,
    *,
    query: dict[str, str | int | float | None] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 20,
) -> Any:
    if query:
        filtered = {key: value for key, value in query.items() if value is not None}
        separator = "&" if "?" in url else "?"
        url = f"{url}{separator}{urlencode(filtered)}"

    request = Request(url, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise ApiError(f"HTTP {exc.code} from {url}: {detail}") from exc
    except URLError as exc:
        raise ApiError(f"Could not reach {url}: {exc.reason}") from exc

    try:
        return json.loads(payload)
    except json.JSONDecodeError as exc:
        raise ApiError(f"Invalid JSON from {url}") from exc
