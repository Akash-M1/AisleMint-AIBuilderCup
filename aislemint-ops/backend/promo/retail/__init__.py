"""Retail namespace contributed by Core, Admin and Data."""
from pathlib import Path
from promo import __path__ as _roots
__path__ = [str(Path(p) / 'retail') for p in _roots if (Path(p) / 'retail').is_dir()]
