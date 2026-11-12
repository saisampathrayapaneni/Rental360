"""LLM client seam for the Rental360 assistant.

This module is a stub on purpose. The deterministic assistant in
``app/assistant.py`` does NOT call any LLM today: it parses the question
with regex/keyword rules and runs fixed, read-only SQL against the
warehouse, so every number it reports is traceable to the database.

When a real provider is wanted, implement a provider here behind this
same ``LLMClient.complete(prompt)`` interface, for example:

    # app/llm_client.py (provider implementation sketch)
    import os
    from openai import OpenAI

    class LLMClient:
        def __init__(self):
            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise RuntimeError("Set OPENAI_API_KEY to enable the LLM provider.")
            self._client = OpenAI(api_key=api_key)

        def complete(self, prompt: str) -> str:
            resp = self._client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
            )
            return resp.choices[0].message.content or ""

An Anthropic-backed variant is the same shape, keyed off
``ANTHROPIC_API_KEY`` instead. Until such a provider is wired in and the
assistant is explicitly switched over to it, ``complete()`` raises
``NotImplementedError`` — the assistant must never pretend an LLM
answered when it did not.
"""

from __future__ import annotations


class LLMClient:
    """Client for an external LLM provider.

    No provider is configured in this project today. Calling
    :meth:`complete` raises :class:`NotImplementedError` with instructions
    for plugging one in.
    """

    def complete(self, prompt: str) -> str:
        """Send *prompt* to the configured LLM provider and return its reply.

        Raises:
            NotImplementedError: always, until a provider implementation
                is added. Set ``OPENAI_API_KEY`` (or ``ANTHROPIC_API_KEY``)
                and implement a provider behind this method — see the
                module docstring for a sketch. The deterministic assistant
                in ``app/assistant.py`` does not use this method.
        """
        raise NotImplementedError(
            "No LLM provider is configured for Rental360. The assistant is "
            "deterministic and does not call any LLM. To plug one in, set "
            "OPENAI_API_KEY (or ANTHROPIC_API_KEY) and implement a provider "
            "behind LLMClient.complete() — see the module docstring in "
            "app/llm_client.py for a sketch."
        )
