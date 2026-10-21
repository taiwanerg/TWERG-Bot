import math

CDI_MAP = [
    (0.5,      '#4b5563', '0',   '無感'),
    (1.5,      '#6cbb6c', '1',   '微震'),
    (2.5,      '#00AAFF', '2',   '輕震'),
    (3.5,      '#0041FF', '3',   '弱震'),
    (4.5,      '#FAE696', '4',   '中震'),
    (5.0,      '#FFE600', '5弱', '強震'),
    (5.5,      '#FF9900', '5強', '強震'),
    (6.0,      '#FF2800', '6弱', '烈震'),
    (6.5,      '#A50021', '6強', '烈震'),
    (math.inf, '#B40068', '7',   '劇震'),
]
GRADES      = [g for _, _, g, _ in CDI_MAP]
GRADE_STYLE = {g: (col, label) for _, col, g, label in CDI_MAP}
GRADE_ALIAS = {'5-': '5弱', '5+': '5強', '6-': '6弱', '6+': '6強'}
GRADE_CDI   = {'0': 0.0, '1': 1.0, '2': 2.0, '3': 3.0, '4': 4.0, '5弱': 4.75, '5強': 5.25, '6弱': 5.75, '6強': 6.25, '7': 6.75}
GRADE_EMOJI = {'0': '⚫', '1': '🟢', '2': '🔵', '3': '🟦', '4': '🟡', '5弱': '🟨', '5強': '🟠', '6弱': '🔴', '6強': '🟥', '7': '🟣'}
NOT_FELT_EMOJI = '⭕'
FULLWIDTH = str.maketrans('01234567', '０１２３４５６７')

def norm(s):
    return (s or '').replace('臺', '台').replace('台灣省', '').strip()

def cdi_style(cdi):
    for maxc, col, grade, label in CDI_MAP:
        if cdi < maxc:
            return col, grade, label
    return CDI_MAP[-1][1], CDI_MAP[-1][2], CDI_MAP[-1][3]

def grade_of(r):
    g = str(r.get('grade') or '')
    g = GRADE_ALIAS.get(g, g)
    if g in GRADE_STYLE:
        return g
    return cdi_style(r.get('cdi') or 0.0)[1]

def town_rank(r):
    return (0 if r.get('allNotFelt') else 1, GRADES.index(grade_of(r)), r.get('cdi') or 0.0)

def merge_town_cdi(town_cdi, discord_reports=None):
    towns = list(town_cdi or [])
    for dr in discord_reports or []:
        grade = GRADE_ALIAS.get(str(dr.get('grade')), str(dr.get('grade')))
        towns.append({
            'countyName': dr.get('county'),
            'townName': dr.get('town'),
            'grade': grade,
            'cdi': GRADE_CDI.get(grade, 0.0),
            'reportCount': 1,
            'isSuspect': False,
            'isDiscord': True
        })
    merged = {}
    for t in towns:
        key = f"{norm(t.get('countyName'))}|{t.get('townName') or ''}"
        if key == '|': continue
        if key not in merged or town_rank(t) > town_rank(merged[key]):
            merged[key] = t
    return sorted(merged.values(), key=town_rank, reverse=True)

def town_lines(towns, limit):
    lines = []
    for t in towns[:limit]:
        if t.get('allNotFelt') and not t.get('isDiscord'):
            emoji, text = NOT_FELT_EMOJI, '沒感覺'
        else:
            grade = grade_of(t)
            emoji = GRADE_EMOJI[grade]
            fw = grade.translate(FULLWIDTH)
            text = fw if len(grade) > 1 else f'{fw}級'
        suspect_mark = ' `⚠️`' if t.get('isSuspect') else ''
        discord_mark = ' `💬`' if t.get('isDiscord') else ''
        lines.append(f"`{emoji}` {text}　{t.get('countyName', '')} {t.get('townName', '')}{suspect_mark}{discord_mark}")
    return lines

def report_count_text(meta):
    total = meta.get('totalReports', 0) or 0
    felt = meta.get('feltReports')
    not_felt = meta.get('notFeltReports')
    if felt is None or not_felt is None:
        return f'{total} 份'
    return f'{total} 份（有感 {felt}、沒感覺 {not_felt}）'
