from django.db import transaction
from .models import Account, AccountOwner, Category, HouseholdSettings, Membership


def _default_owners(household):
    membership=(household.memberships.select_related('user').filter(role=Membership.Role.OWNER).first()
                or household.memberships.select_related('user').first())
    user=membership.user if membership else None
    primary_name=((user.get_full_name() if user else '') or (user.username if user else 'Primary owner')).strip()
    primary,_=AccountOwner.objects.get_or_create(
        household=household,name=primary_name,
        defaults={'kind':AccountOwner.Kind.PERSON,'linked_user':user,'sort_order':10},
    )
    if user and primary.linked_user_id is None:
        primary.linked_user=user; primary.save(update_fields=['linked_user'])
    joint,_=AccountOwner.objects.get_or_create(
        household=household,name='Joint',
        defaults={'kind':AccountOwner.Kind.JOINT,'sort_order':30},
    )
    system,_=AccountOwner.objects.get_or_create(
        household=household,name='System / External',
        defaults={'kind':AccountOwner.Kind.SYSTEM,'sort_order':900},
    )
    return {'primary':primary,'joint':joint,'system':system}


STARTER_CATEGORIES={
    Category.Kind.FUNDING:['Employment','Other income'],
    Category.Kind.EXPENSE:['Housing','Utilities','Groceries','Transport','Subscriptions','Health','Personal','Gifts','Travel','Other'],
    Category.Kind.SAVINGS:['Emergency fund','General savings'],
}


@transaction.atomic
def seed_household(household, load_plan=True):
    """Create a small neutral starter setup for a new household.

    ``load_plan`` is retained as the historical argument name for compatibility;
    in the public distribution it means "create starter categories". No personal
    budget amounts or example transactions are loaded.
    """
    settings,_=HouseholdSettings.objects.get_or_create(household=household)
    owners=_default_owners(household)
    defaults=[
        ('Main account',Account.Type.CHECKING,Account.Purpose.AVAILABLE,owners['primary'],True),
        ('Savings',Account.Type.SAVINGS,Account.Purpose.SAVINGS,owners['primary'],True),
        ('Outside world',Account.Type.EXTERNAL,Account.Purpose.EXTERNAL,owners['system'],False),
    ]
    for name,typ,purpose,owner,nw in defaults:
        account,_=Account.objects.get_or_create(
            household=household,name=name,
            defaults={'account_type':typ,'purpose':purpose,'owner':owner,'include_in_net_worth':nw,'currency':settings.base_currency},
        )
        if not account.owners.exists(): account.owners.set([owner])
    if not load_plan:return
    sort=10
    for kind,names in STARTER_CATEGORIES.items():
        for name in names:
            Category.objects.get_or_create(household=household,kind=kind,name=name,defaults={'sort_order':sort})
            sort+=10
