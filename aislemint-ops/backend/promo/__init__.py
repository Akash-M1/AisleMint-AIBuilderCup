"""Source-checkout loader for the repository composition (no domain code)."""
from pathlib import Path
from pkgutil import extend_path
_ROOT = Path(__file__).resolve().parents[3]
__path__ = extend_path(__path__, __name__)
for _name in ('core','admin','promotions','demand','sales','data','connect'):
    _path = _ROOT / ('aislemint-' + _name) / 'backend' / 'promo'
    if _path.is_dir(): __path__.append(str(_path))
