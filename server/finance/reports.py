from collections import defaultdict
from decimal import Decimal
from datetime import timedelta
from django.db.models import Sum
from .models import BudgetImpact, Category, ZERO
from .services import net_worth_at
from .utils import add_months

def monthly_series(household,start_month,end_month):
    months=[]; d=start_month
    while d<=end_month:
        nxt=add_months(d,1)
        qs=BudgetImpact.objects.filter(transaction__household=household,budget_date__gte=d,budget_date__lt=nxt).values('kind').annotate(total=Sum('amount'))
        vals={r['kind']:r['total'] or ZERO for r in qs}
        months.append({'month':d,'funding':vals.get('funding',ZERO),'expenses':vals.get('expense',ZERO),'savings':vals.get('savings',ZERO),'surplus':vals.get('funding',ZERO)-vals.get('expense',ZERO)-vals.get('savings',ZERO),'net_worth':net_worth_at(household,nxt.replace(day=1)-timedelta(days=1))})
        d=nxt
    return months

def category_expenses(household,start_date,end_date):
    rows=BudgetImpact.objects.filter(transaction__household=household,kind=BudgetImpact.Kind.EXPENSE,budget_date__gte=start_date,budget_date__lte=end_date).values('category__name').annotate(total=Sum('amount')).order_by('-total')
    return list(rows)
