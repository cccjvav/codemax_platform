"""Explicit, low-volume live probes. Never run from the normal pytest/CI suite.

Run from the repository root: python scripts/probe_llm.py models|chat|mermaid|embeddings
Reads the private root .env; environment variables take precedence. No key CLI flag.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.config import Settings  # noqa: E402
from app.tools.llm import LLMClient, LLMError, generate_mermaid  # noqa: E402


def configured_client(mode: str) -> LLMClient:
    """Read only model configuration; reject credential-bearing or insecure URLs."""
    config = Settings(_env_file=ROOT / '.env')
    key = config.LLM_API_KEY.strip()
    base = config.LLM_BASE_URL.strip().rstrip('/')
    model = config.LLM_MODEL.strip()
    embed = config.LLM_EMBED_MODEL.strip()
    if 'LLM_BASE_URL' not in config.model_fields_set:
        raise ValueError('Explicitly configure the intended provider LLM_BASE_URL')
    if not key:
        raise ValueError('Missing LLM_API_KEY in private .env or environment')
    parsed = urlsplit(base)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None
            or parsed.password is not None or parsed.query or parsed.fragment):
        raise ValueError('LLM_BASE_URL must be an HTTPS base URL without credentials/query/fragment')
    _ = parsed.port  # Reject invalid ports before any request.
    if mode in {'chat', 'mermaid'} and (not model or 'LLM_MODEL' not in config.model_fields_set):
        raise ValueError('Set LLM_MODEL to an exact provider-supported chat model ID')
    if mode == 'embeddings' and (not embed or 'LLM_EMBED_MODEL' not in config.model_fields_set):
        raise ValueError('Set LLM_EMBED_MODEL to a confirmed embedding model ID')
    return LLMClient(api_key=key, base_url=base, model=model, embed_model=embed, timeout=30.0)


async def probe(mode: str, client: LLMClient) -> None:
    """One request per mode; fixed non-sensitive prompts, no DB, no automatic retries."""
    if mode == 'models':
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False, transport=client.transport) as http:
            response = await http.get(client.base_url + '/models', headers={'Authorization': 'Bearer ' + client.api_key})
        if response.status_code != 200:
            raise ValueError(f'Model listing HTTP {response.status_code}; response body withheld')
        data = response.json()
        rows = data.get('data') if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            raise ValueError('Model listing must contain a non-empty data array')
        if any(not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id'].strip() for row in rows):
            raise ValueError('Each model entry must contain a non-empty string id')
        print('MODEL IDS (listing alone does not prove chat/embedding support):')
        for row in rows:
            # Treat external IDs as data, not terminal control sequences; redact even unexpected echoes.
            print(ascii(row['id'].replace(client.api_key, '[REDACTED]'))[:200])
    elif mode == 'chat':
        answer = await client.chat('Reply briefly to this harmless connectivity check.', 'Reply with OK.')
        print(f'CHAT PASS: valid non-empty response ({len(answer)} characters); content withheld')
    elif mode == 'mermaid':
        diagram = await generate_mermaid('A customer has many orders. Draw two classes and their relationship.', client)
        print(f'MERMAID PREFIX PASS: {len(diagram)} characters; browser syntax/rendering still needs acceptance')
    elif mode == 'embeddings':
        vectors = await client.embeddings(['customer order', 'customer purchase'])
        print(f'EMBEDDINGS PASS: {len(vectors)} vectors, dimension {len(vectors[0])}; not threshold calibration')
    else:
        raise ValueError('Unknown probe mode')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('models', 'chat', 'mermaid', 'embeddings'))
    args = parser.parse_args(argv)
    try:
        client = configured_client(args.mode)
        asyncio.run(probe(args.mode, client))
    except (httpx.HTTPError, LLMError, ValueError, OSError) as error:
        # Neither response bodies nor exception messages are printed: upstream errors may echo secrets.
        print(f'PROBE FAILED: {type(error).__name__}; no success claimed. See Windows guide troubleshooting.', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
