from .permissions import get_membership

def household_context(request):
    if not getattr(request,'user',None) or not request.user.is_authenticated:
        return {}
    m=get_membership(request.user)
    if not m: return {'current_household':None,'current_membership':None}
    try: settings=m.household.settings
    except Exception: settings=None
    return {'current_household':m.household,'current_membership':m,'household_settings':settings}
