from functools import lru_cache
from pathlib import Path
from django.conf import settings

@lru_cache(maxsize=1)
def get_version():
    try:
        return (Path(settings.BASE_DIR) / 'VERSION').read_text(encoding='utf-8').strip() or 'unknown'
    except OSError:
        return 'unknown'
