from datetime import datetime
from zoneinfo import ZoneInfo

DEFAULT_WORKING_HOURS = {'enabled': True, 'start': '07:00', 'end': '17:00',
                         'timezone': 'Asia/Jakarta', 'days': [0, 1, 2, 3, 4]}


def validate_working_hours(value=None):
    raw = dict(DEFAULT_WORKING_HOURS if value is None else value)
    if type(raw.get('enabled')) is not bool:
        raise ValueError('Working hours enabled must be boolean')
    for key in ('start', 'end'):
        text = raw.get(key, '')
        if not isinstance(text, str) or len(text) != 5 or datetime.strptime(text, '%H:%M').strftime('%H:%M') != text:
            raise ValueError('Working hours must use HH:MM')
    if raw['start'] >= raw['end']:
        raise ValueError('Working hours end must be after start')
    days = raw.get('days')
    if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
        raise ValueError('Working days must contain weekday numbers 0-6')
    try:
        ZoneInfo(raw['timezone'])
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError('Invalid working hours timezone') from exc
    return {key: raw[key] for key in DEFAULT_WORKING_HOURS}


def within_working_hours(value=None, now=None):
    rule = validate_working_hours(value)
    if not rule['enabled']:
        return True
    zone = ZoneInfo(rule['timezone'])
    current = datetime.now(zone) if now is None else (now.replace(tzinfo=zone) if now.tzinfo is None else now.astimezone(zone))
    return current.weekday() in rule['days'] and rule['start'] <= current.strftime('%H:%M') < rule['end']
