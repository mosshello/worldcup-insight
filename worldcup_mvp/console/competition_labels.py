"""Display labels only; these never register or enable a training competition."""
LABELS = {
    'AFC Champions League Two': ('亚冠二级', '洲际杯赛'),
    'Austrian Bundesliga': ('奥甲', '联赛'), 'Belgian Pro League': ('比甲', '联赛'),
    'Brazilian Serie A': ('巴甲', '联赛'), 'Taca de Portugal': ('葡萄牙杯', '国内杯赛'),
    'Thai League 1': ('泰超', '联赛'), 'UEFA Champions League Qualifying': ('欧冠资格赛', '洲际杯赛'),
    'English Premier League': ('英超', '联赛'), 'English League Championship': ('英冠', '联赛'),
    'Spanish LALIGA': ('西甲', '联赛'), 'Spanish LaLiga': ('西甲', '联赛'),
    'German Bundesliga': ('德甲', '联赛'), 'German Cup': ('德国杯', '国内杯赛'),
    'Italian Serie A': ('意甲', '联赛'), 'Coppa Italia': ('意大利杯', '国内杯赛'),
    'French Ligue 1': ('法甲', '联赛'), 'French Ligue 2': ('法乙', '联赛'),
    'UEFA Champions League': ('欧冠', '洲际杯赛'), 'UEFA Europa League': ('欧联杯', '洲际杯赛'),
    'UEFA Conference League': ('欧协联', '洲际杯赛'),
    'AFC Champions League Elite': ('亚冠精英', '洲际杯赛'),
    'AFC Champions League': ('亚冠', '洲际杯赛'),
    'Australian A-League Men': ('澳超', '联赛'), 'Chinese Super League': ('中超', '联赛'),
    'Japanese J.League': ('日职', '联赛'), 'Japanese J1 League': ('日职', '联赛'),
    'South Korean K League 1': ('韩职', '联赛'), 'Chinese FA Cup': ('中国足协杯', '国内杯赛'),
    'Turkish Super Lig': ('土超', '联赛'), 'Dutch Eredivisie': ('荷甲', '联赛'),
    'Portuguese Primeira Liga': ('葡超', '联赛'), 'Norwegian Eliteserien': ('挪超', '联赛'),
    'Swedish Allsvenskan': ('瑞超', '联赛'), 'Club Friendly': ('俱乐部友谊赛', '友谊赛'),
    'English Carabao Cup': ('英联杯', '国内杯赛'), 'English FA Cup': ('英足总杯', '国内杯赛'),
    'Spanish Copa del Rey': ('西班牙国王杯', '国内杯赛'),
}
CHINESE_TYPES = {label: kind for label, kind in LABELS.values()}
CHINESE_TYPES.update({'亚冠杯':'洲际杯赛','中协杯':'国内杯赛','日联杯':'国内杯赛','天皇杯':'国内杯赛',
                     '韩足总':'国内杯赛','球会友谊':'友谊赛','荷兰杯':'国内杯赛','葡杯':'国内杯赛',
                     'K1联赛':'联赛','东盟锦标':'洲际杯赛','意杯':'国内杯赛','捷克杯':'国内杯赛',
                     '捷甲':'联赛','泰足总':'国内杯赛','葡联杯':'国内杯赛','西班牙杯':'国内杯赛','阿塞联':'联赛',
                     '芬超':'联赛','芬兰杯':'国内杯赛'})


def label(name):
    if name in LABELS:
        short, kind = LABELS[name]
    elif name in CHINESE_TYPES:
        short, kind = name, CHINESE_TYPES[name]
    else:
        short, kind = name or '赛事待核实', '类型待核实'
    return {'league': short, 'competition_type': kind, 'league_original': name}
