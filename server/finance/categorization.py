"""Smart but explainable transaction categorisation.

The engine deliberately prefers explicit user rules and previously confirmed
choices over built-in heuristics. Built-in rules are conservative and use
worldwide/European merchant and banking terminology. If nothing strong matches,
we fall back to an explicit generic category (for example ``Other`` or
``Other income``) instead of silently choosing the first category in the
household.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
import json
from pathlib import Path
import re
import unicodedata

from .models import BankSyncTransaction, BudgetImpact, Category, ImportRule, Transaction


@dataclass(frozen=True)
class CategorySuggestion:
    transaction_kind: str
    impact_kind: str
    category: Category | None
    source: str
    confidence: str
    reason: str


# Ordered, data-driven rule table. ``aliases`` are category names we try to
# resolve in the user's household; later aliases are increasingly generic.
RULES_PATH = Path(__file__).with_name('category_rules.json')
BUILTIN_RULES = tuple(json.loads(RULES_PATH.read_text(encoding='utf-8'))['rules'])

LEGAL_SUFFIXES = {
    'gmbh', 'ug', 'ag', 'kg', 'ltd', 'limited', 'plc', 'inc', 'corp',
    'corporation', 'llc', 'sa', 'sarl', 'srl', 'spa', 'bv', 'nv', 'oy', 'ab',
}

GENERIC_MERCHANT_WORDS = {
    'payment', 'card', 'purchase', 'transaction', 'transfer', 'direct', 'debit',
    'credit', 'sepa', 'bank', 'online', 'mobile', 'visa', 'mastercard',
}

FALLBACK_ALIASES = {
    Category.Kind.FUNDING: ('Other income', 'Misc income', 'Other', 'Uncategorized'),
    Category.Kind.EXPENSE: ('Other', 'Miscellaneous', 'Uncategorized'),
}


def normalize_text(value: str | None) -> str:
    value = unicodedata.normalize('NFKD', str(value or ''))
    value = ''.join(ch for ch in value if not unicodedata.combining(ch)).casefold()
    value = value.replace('&', ' and ')
    value = re.sub(r'[^a-z0-9]+', ' ', value)
    return re.sub(r'\s+', ' ', value).strip()


def _contains_phrase(text: str, phrase: str) -> bool:
    text = f' {normalize_text(text)} '
    phrase = normalize_text(phrase)
    return bool(phrase) and f' {phrase} ' in text


def merchant_key(counterparty: str | None, description: str | None) -> str:
    source = counterparty or description or ''
    tokens = normalize_text(source).split()
    cleaned = []
    for token in tokens:
        if token in LEGAL_SUFFIXES or token in GENERIC_MERCHANT_WORDS:
            continue
        if token.isdigit() and len(token) >= 2:
            continue
        if len(token) >= 12 and any(ch.isdigit() for ch in token):
            continue
        cleaned.append(token)
    return ' '.join(cleaned[:8])


def _counterparty_account_key(raw_data, amount) -> str:
    if not isinstance(raw_data, dict):
        return ''
    side = 'debtor' if amount > 0 else 'creditor'
    candidates = [
        raw_data.get(f'{side}_account'),
        (raw_data.get(side) or {}).get('account') if isinstance(raw_data.get(side), dict) else None,
    ]
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        for key in ('iban', 'identification', 'bban', 'masked_iban', 'id'):
            value = candidate.get(key)
            if value:
                normalized = re.sub(r'[^A-Za-z0-9]', '', str(value)).upper()
                if len(normalized) >= 6:
                    return normalized
    return ''


def _direction(amount) -> str:
    return 'in' if amount > 0 else 'out'


def _kinds(amount):
    if amount > 0:
        return Transaction.Kind.INCOME, BudgetImpact.Kind.FUNDING
    return Transaction.Kind.EXPENSE, BudgetImpact.Kind.EXPENSE


def _resolve_category(household, kind: str, aliases) -> Category | None:
    categories = list(Category.objects.filter(household=household, kind=kind, is_active=True))
    by_name = {normalize_text(c.name): c for c in categories}
    normalized_aliases=[normalize_text(alias) for alias in aliases if normalize_text(alias)]
    for alias in normalized_aliases:
        found = by_name.get(alias)
        if found:
            return found
    # Community/user category names often add qualifiers (for example
    # “Groceries & household”). Prefer an unambiguous token/phrase match.
    for alias in normalized_aliases:
        if len(alias) < 4:
            continue
        matches=[c for c in categories if _contains_phrase(c.name,alias)]
        if len(matches)==1:
            return matches[0]
    return None


def _rule_matches(rule, description, amount):
    direction = _direction(amount)
    if rule.direction not in ('any', direction):
        return False
    text = description or ''
    pattern = rule.pattern or ''
    if rule.match_type == 'contains':
        return pattern.casefold() in text.casefold()
    if rule.match_type == 'starts':
        return text.casefold().startswith(pattern.casefold())
    if rule.match_type == 'exact':
        return text.casefold() == pattern.casefold()
    try:
        return re.search(pattern, text, re.I) is not None
    except re.error:
        return False


def _actual_category(row, expected_kind: str) -> Category | None:
    tx = row.transaction
    if not tx:
        return None
    impacts = [i for i in tx.budget_impacts.all() if i.kind == expected_kind and i.category_id]
    if len(impacts) != 1:
        return None
    return impacts[0].category


def _pick_history_category(rows, expected_kind, *, account_key='', party_key='', merchant=''):
    matches = []
    source = ''
    confidence = ''

    if account_key:
        for row in rows:
            if _counterparty_account_key(row.raw_data, row.amount) == account_key:
                category = _actual_category(row, expected_kind)
                if category:
                    matches.append(category)
        if matches:
            source, confidence = 'learned_account', 'high'

    if not matches and party_key:
        for row in rows:
            if normalize_text(row.counterparty) == party_key:
                category = _actual_category(row, expected_kind)
                if category:
                    matches.append(category)
        if matches:
            source, confidence = 'learned_counterparty', 'high'

    if not matches and merchant and len(merchant) >= 4:
        for row in rows:
            if merchant_key(row.counterparty, row.description) == merchant:
                category = _actual_category(row, expected_kind)
                if category:
                    matches.append(category)
        if matches:
            source, confidence = 'learned_merchant', 'medium'

    if not matches:
        return None, '', ''

    counts = Counter(c.pk for c in matches)
    ordered = counts.most_common(2)
    if len(ordered) > 1 and ordered[0][1] == ordered[1][1]:
        return None, '', ''
    winner_id = ordered[0][0]
    winner = next(c for c in matches if c.pk == winner_id)
    return winner, source, confidence


def load_categorization_history(household, limit=300):
    return list(
        BankSyncTransaction.objects.filter(
            bank_account__connection__household=household,
            status=BankSyncTransaction.Status.IMPORTED,
            transaction__isnull=False,
        ).select_related('transaction').prefetch_related(
            'transaction__budget_impacts__category'
        ).order_by('-booking_date', '-id')[:limit]
    )


def categorize_import_row(
    household,
    description,
    amount,
    *,
    counterparty='',
    raw_data=None,
    bank_account=None,
    exclude_row_id=None,
    history_rows=None,
) -> CategorySuggestion:
    transaction_kind, impact_kind = _kinds(amount)

    # Explicit household rules always win over learned/built-in behaviour.
    for rule in ImportRule.objects.filter(household=household, is_active=True).select_related('category'):
        if _rule_matches(rule, description, amount):
            return CategorySuggestion(
                rule.transaction_kind or transaction_kind,
                rule.impact_kind or (rule.category.kind if rule.category_id else impact_kind),
                rule.category,
                'user_rule',
                'high',
                f'Matched your import rule “{rule.name}”.',
            )

    # Learn only from transactions the user actually imported and categorised.
    # We use the resulting BudgetImpact rather than the historical suggestion,
    # which protects users upgrading from the old Rent/Employment fallback bug.
    history = load_categorization_history(household) if history_rows is None else list(history_rows)
    if exclude_row_id:
        history = [row for row in history if row.pk != exclude_row_id]

    account_key = _counterparty_account_key(raw_data, amount)
    party_key = normalize_text(counterparty)
    merchant = merchant_key(counterparty, description)
    learned, source, confidence = _pick_history_category(
        history,
        impact_kind,
        account_key=account_key,
        party_key=party_key,
        merchant=merchant,
    )
    if learned:
        reason = {
            'learned_account': 'Matched a counterparty account you categorised before.',
            'learned_counterparty': 'Matched a counterparty you categorised before.',
            'learned_merchant': 'Matched a merchant you categorised before.',
        }[source]
        return CategorySuggestion(transaction_kind, impact_kind, learned, source, confidence, reason)

    combined = ' '.join(x for x in (counterparty, description) if x)
    direction = _direction(amount)
    for rule in BUILTIN_RULES:
        if rule['direction'] != direction:
            continue
        if not any(_contains_phrase(combined, pattern) for pattern in rule['patterns']):
            continue
        category = _resolve_category(household, impact_kind, rule['aliases'])
        if category:
            return CategorySuggestion(
                transaction_kind,
                impact_kind,
                category,
                'builtin_rule',
                'medium',
                f'Matched the built-in {rule["name"]} rule.',
            )

    fallback = _resolve_category(household, impact_kind, FALLBACK_ALIASES.get(impact_kind, ()))
    if fallback:
        return CategorySuggestion(
            transaction_kind,
            impact_kind,
            fallback,
            'fallback',
            'low',
            f'No strong match; using {fallback.name} as the safe fallback.',
        )

    return CategorySuggestion(
        transaction_kind,
        impact_kind,
        None,
        'none',
        'none',
        'No confident category suggestion is available.',
    )


def refresh_bank_row_suggestion(row, *, persist=True, history_rows=None) -> CategorySuggestion:
    suggestion = categorize_import_row(
        row.bank_account.connection.household,
        row.description,
        row.amount,
        counterparty=row.counterparty,
        raw_data=row.raw_data,
        bank_account=row.bank_account,
        exclude_row_id=row.pk,
        history_rows=history_rows,
    )
    changed = (
        row.suggested_kind != suggestion.transaction_kind
        or row.suggested_impact_kind != suggestion.impact_kind
        or row.suggested_category_id != (suggestion.category.pk if suggestion.category else None)
    )
    row.suggested_kind = suggestion.transaction_kind
    row.suggested_impact_kind = suggestion.impact_kind
    row.suggested_category = suggestion.category
    row.suggestion_source = suggestion.source
    row.suggestion_confidence = suggestion.confidence
    row.suggestion_reason = suggestion.reason
    if persist and changed and row.pk and row.status == BankSyncTransaction.Status.PENDING:
        row.save(update_fields=['suggested_kind', 'suggested_impact_kind', 'suggested_category', 'updated_at'])
    return suggestion
