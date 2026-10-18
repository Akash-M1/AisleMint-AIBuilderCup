"""Resolve the composition root without coupling assets to a repository's path."""
import os
from pathlib import Path

def workspace_root():
    configured = os.environ.get('AISLEMINT_WORKSPACE_ROOT')
    if configured: return Path(configured).resolve()
    for parent in Path(__file__).resolve().parents:
        if (parent / 'aislemint-ops' / 'workspace.json').is_file(): return parent
    # Installed distributions use an explicitly configured working directory.
    return Path.cwd().resolve()

ROOT = workspace_root()
OPS_ROOT = ROOT / 'aislemint-ops'
# Production injects environment variables; local secrets belong to Ops.
ENV_FILE = Path(os.environ.get('AISLEMINT_ENV_FILE', str(OPS_ROOT / '.env'))).expanduser()
