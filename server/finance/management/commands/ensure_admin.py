import os
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
class Command(BaseCommand):
    def handle(self,*args,**kwargs):
        username=os.getenv('BUDGET_ADMIN_USERNAME','').strip(); password=os.getenv('BUDGET_ADMIN_PASSWORD',''); email=os.getenv('BUDGET_ADMIN_EMAIL','').strip()
        if not username or not password:
            self.stdout.write('Admin env vars not set; skipping automatic admin creation.'); return
        if password.startswith('CHANGE_ME'):
            raise RuntimeError('Set a strong BUDGET_ADMIN_PASSWORD before first start.')
        User=get_user_model(); user,created=User.objects.get_or_create(username=username,defaults={'email':email,'is_staff':True,'is_superuser':True})
        changed=False
        if not user.is_staff or not user.is_superuser: user.is_staff=True; user.is_superuser=True; changed=True
        if created or os.getenv('BUDGET_ADMIN_RESET_PASSWORD','0')=='1': user.set_password(password); changed=True
        if email and user.email!=email: user.email=email; changed=True
        if changed: user.save()
        self.stdout.write(self.style.SUCCESS(f'Admin {username} ready.'))
