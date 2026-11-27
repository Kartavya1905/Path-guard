"""AI provider for optional suggestions. Only Ollama is implemented (free, local).

A provider is "remote" unless its host is this machine. A remote provider may only
receive project data when both PATHGUARD_EXTERNAL_PROCESSING_ENABLED and the
project's own external-processing opt-in are on.
"""

import json
import urllib.error
import urllib.request
from typing import Protocol
from urllib.parse import urlparse

from app.config import Settings

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}


class ProviderError(Exception):
    """The model couldn't be reached or answered badly. The core workflow is unaffected."""


class Provider(Protocol):
    name: str
    model: str
    remote: bool

    def complete(self, messages: list[dict[str, str]], schema: dict, max_tokens: int,
                 timeout: float) -> str: ...


def is_local(url: str) -> bool:
    return (urlparse(url).hostname or "") in LOCAL_HOSTS


class OllamaProvider:
    name = "ollama"

    def __init__(self, base_url: str, model: str):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.remote = not is_local(base_url)

    def complete(self, messages, schema, max_tokens, timeout) -> str:
        body = json.dumps({
            "model": self.model, "messages": messages, "stream": False, "format": schema,
            "options": {"temperature": 0, "num_predict": max_tokens},
        }).encode()
        req = urllib.request.Request(f"{self.base_url}/api/chat", data=body, method="POST",
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as res:
                payload = json.loads(res.read())
        except TimeoutError:
            raise ProviderError(f"The model didn't answer within {timeout:g} seconds.") from None
        except urllib.error.HTTPError as e:
            raise ProviderError(f"The model service returned an error ({e.code}).") from None
        except (urllib.error.URLError, OSError):
            raise ProviderError("The model service isn't reachable. Is Ollama running?") from None
        except json.JSONDecodeError:
            raise ProviderError("The model service sent an unreadable reply.") from None
        content = (payload.get("message") or {}).get("content")
        if not isinstance(content, str):
            raise ProviderError("The model service sent a reply without content.")
        return content


def get_provider(settings: Settings) -> Provider:
    return OllamaProvider(settings.ai_base_url, settings.ai_model)
