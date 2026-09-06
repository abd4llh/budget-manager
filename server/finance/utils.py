from datetime import date
import calendar

def month_start(year, month): return date(year, month, 1)
def add_months(d, months):
    idx=(d.year*12+d.month-1)+months
    return date(idx//12,idx%12+1,1)
def clamp_day(year, month, day): return min(day,calendar.monthrange(year,month)[1])
def advance_date(d, frequency, interval=1):
    if frequency=='weekly':
        from datetime import timedelta
        return d+timedelta(weeks=interval)
    if frequency=='yearly':
        try: return d.replace(year=d.year+interval)
        except ValueError: return d.replace(year=d.year+interval,day=28)
    nxt=add_months(date(d.year,d.month,1),interval)
    return nxt.replace(day=clamp_day(nxt.year,nxt.month,d.day))
