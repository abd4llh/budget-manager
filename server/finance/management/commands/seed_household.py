from django.core.management.base import BaseCommand,CommandError
from finance.models import Household
from finance.seed import seed_household
class Command(BaseCommand):
    def add_arguments(self,p): p.add_argument('household_id',type=int)
    def handle(self,*args,**o):
        try:h=Household.objects.get(pk=o['household_id'])
        except Household.DoesNotExist: raise CommandError('Household not found')
        seed_household(h,True); self.stdout.write(self.style.SUCCESS('Seeded.'))
