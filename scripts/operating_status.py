"""Gas operating evidence policy v1; keep aligned with dashboard status-policy.js."""
import re
from datetime import datetime, timezone, timedelta
UNCERTAINTY = re.compile(r'presum|not confirmed|not independently confirmed|unconfirmed|data gap|unknown|unavailable|no .*disruption identified', re.I)

def classify(row, now=None):
    status = row.get('map_status')
    if status not in {'Green', 'Amber', 'Red', 'Blue', 'Unknown'}:
        return 'Unknown'
    if status != 'Green':
        return status
    if UNCERTAINTY.search(' '.join(str(row.get(k) or '') for k in ('status', 'watch_item'))):
        return 'Unknown'
    date = row.get('verified_at') or ''
    if row.get('verification') != 'confirmed' or not row.get('source') or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', date):
        return 'Unknown'
    try:
        verified = datetime.strptime(date, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    except ValueError:
        return 'Unknown'
    now = now or datetime.now(timezone.utc)
    return 'Green' if timedelta(0) <= now - verified <= timedelta(days=30) else 'Unknown'
