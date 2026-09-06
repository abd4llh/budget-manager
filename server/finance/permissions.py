from functools import wraps
from django.contrib import messages
from django.shortcuts import redirect
from .models import Membership

def get_membership(user):
    return Membership.objects.filter(user=user).select_related('household').first()
def current_household(user):
    m=get_membership(user); return m.household if m else None

def require_role(*roles):
    def deco(view):
        @wraps(view)
        def wrapped(request,*args,**kwargs):
            m=get_membership(request.user)
            if not m: return redirect('setup_household')
            if m.role not in roles:
                messages.error(request,'You do not have permission to change this.')
                return redirect('dashboard')
            request.membership=m; request.household=m.household
            return view(request,*args,**kwargs)
        return wrapped
    return deco
