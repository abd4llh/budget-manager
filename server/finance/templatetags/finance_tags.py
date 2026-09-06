from decimal import Decimal
from django import template
from ..version import get_version
register=template.Library()
@register.filter
def money(value,currency='EUR'):
    try: v=Decimal(value)
    except Exception: v=Decimal('0')
    return f'{v:,.2f} {currency}'
@register.filter
def pct(value):
    try: return f'{Decimal(value):.1f}%'
    except Exception: return '0.0%'
@register.filter
def get_item(mapping,key): return mapping.get(key) if mapping else None

@register.simple_tag
def app_version():
    return get_version()
