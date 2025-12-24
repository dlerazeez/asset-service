def filter_by_cash_access(items, user):
    if user.is_admin:
        return items
    allowed = set(user.allowed_cash_accounts or [])
    return [x for x in items if x.get("paid_through_account_id") in allowed]
