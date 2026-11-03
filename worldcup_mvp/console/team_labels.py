"""Display-only Chinese club names. Never used to join fixtures or training teams."""

import re
import unicodedata

# Exact provider display-name aliases; preserve the original beside every label.
NAMES = {
    "Angers":"昂热", "Avispa Fukuoka":"福冈黄蜂", "CD Artistico Navalcarnero":"纳瓦尔卡内罗",
    "Cerezo Osaka":"大阪樱花", "Cremonese":"克雷莫纳", "Estoril":"埃斯托里尔",
    "Fagiano Okayama":"冈山绿雉", "Fleetwood Town":"弗利特伍德", "Fredrikstad":"腓特烈斯塔",
    "Hapoel Be'er":"贝尔谢巴工人", "Machida Zelvia":"町田泽维亚", "Maritimo":"马里迪莫",
    "Marseille":"马赛", "Paredes":"帕雷德斯", "Shimizu S-Pulse":"清水心跳",
    "Sisaket F.C":"四色菊", "V-Varen Nagasaki":"长崎航海",
    "1. FC Union Berlin":"柏林联合", "AJ Auxerre":"欧塞尔", "AZ Alkmaar":"阿尔克马尔",
    "Aalesund":"奥勒松", "Académico de Viseu":"维塞乌学院", "Ajax Amsterdam":"阿贾克斯",
    "Amed SFK":"阿美德", "Anderlecht":"安德莱赫特", "Annecy":"昂纳西", "Arouca":"阿罗卡",
    "Athletic Club":"毕尔巴鄂竞技", "Atlético Calatayud":"卡拉塔尤德竞技", "Atlético-MG":"米内罗竞技",
    "Auriense Cented Academy":"奥里恩塞森特学院", "Ayutthaya United":"大城联", "BK Häcken":"赫根",
    "Bahia":"巴伊亚", "Besiktas":"贝西克塔斯", "Bodo/Glimt":"博德闪耀", "Borac Banja Luka":"巴尼亚卢卡战士",
    "Botafogo":"博塔弗戈", "C.D. Nacional":"葡萄牙国民", "Casa Pia":"卡萨皮亚", "Caykur Rizespor":"里泽体育",
    "Celtic":"凯尔特人", "Chapecoense":"沙佩科恩斯", "Chongqing Tonglianglong":"重庆铜梁龙",
    "Clermont Foot":"克莱蒙", "Club Brugge":"布鲁日", "Coritiba":"科里蒂巴", "Cruzeiro":"克鲁塞罗",
    "Degerfors IF":"代格福什", "Dijon FCO":"第戎", "Dunkerque":"敦刻尔克", "Dynamo Dresden":"德累斯顿迪纳摩",
    "Eintracht Frankfurt":"法兰克福", "Erzurum BB":"埃尔祖鲁姆", "Estrela":"阿马多拉之星", "Excelsior":"SBV精英",
    "FC Augsburg":"奥格斯堡", "FC Famalicao":"法马利康", "FC Tokyo":"东京FC", "Flamengo":"弗拉门戈",
    "Fluminense":"弗鲁米嫩塞", "Gil Vicente":"吉维森特", "Go Ahead Eagles":"前进之鹰", "Grenoble":"格勒诺布尔",
    "Grêmio":"格雷米奥", "Guingamp":"甘冈", "Halmstads BK":"哈尔姆斯塔德", "Heerenveen":"海伦芬", "Henan":"河南队",
    "IF Brommapojkarna":"布洛马波卡纳", "IF Elfsborg":"埃尔夫斯堡", "IFK Göteborg":"哥德堡", "IK Sirius":"天狼星",
    "Internacional":"巴西国际", "Istanbul Basaksehir":"伊斯坦布尔巴萨克赛尔", "JEF United Ichihara-Chiba":"千叶市原",
    "Johor Darul Ta'zim":"柔佛新山", "Juventus":"尤文图斯", "KFUM Oslo":"奥斯陆KFUM", "KV Mechelen":"梅赫伦",
    "Kairat Almaty":"阿拉木图凯拉特", "Kalmar FF":"卡尔马", "Kashima Antlers":"鹿岛鹿角", "Kawasaki Frontale":"川崎前锋",
    "Kocaelispor":"科贾埃利体育", "LASK Linz":"林茨", "Le Mans":"勒芒", "Lecce":"莱切", "Lech Poznan":"波兹南莱赫",
    "Lorient":"洛里昂", "Metz":"梅斯", "Mirassol":"米拉索尔", "Mjällby AIF":"米亚尔比", "Molde":"莫尔德",
    "Montpellier":"蒙彼利埃", "Moreirense":"莫雷伦斯", "Málaga":"马拉加", "NEC Nijmegen":"奈梅亨",
    "Nagoya Grampus":"名古屋鲸八", "Nice":"尼斯", "Nottingham Forest":"诺丁汉森林", "Omonia Nicosia":"尼科西亚奥莫尼亚",
    "Pafos":"帕福斯", "Palmeiras":"帕尔梅拉斯", "Paris FC":"巴黎FC", "Pau":"波城", "Qingdao Hainiu":"青岛海牛",
    "Racing Santander":"桑坦德竞技", "Rapid Vienna":"维也纳快速", "Ratchaburi FC":"拉查武里", "Rayo Vallecano":"巴列卡诺",
    "Remo":"瑞模", "Rio Ave":"里奥阿维", "Rodez Aveyron":"罗德兹", "SC Cambuur":"坎布尔", "SS Signa":"锡尼亚",
    "SV Josko Ried":"里德", "Saarbrücken":"萨尔布吕肯", "Sabah FK":"萨巴赫", "Saint-Étienne":"圣埃蒂安",
    "Samsunspor":"萨姆松体育", "Sanfrecce Hiroshima":"广岛三箭", "Santa Clara":"圣克拉拉", "Sochaux":"索肖",
    "Stade Laval":"拉瓦勒", "Stade Rennais":"雷恩", "Standard Liege":"标准列日", "Sunderland":"桑德兰",
    "TSV Hartberg":"哈特堡格", "Tondela":"通德拉", "Torino":"都灵", "Torreense":"托伦斯", "Toulouse":"图卢兹",
    "Tromso":"特罗姆瑟", "Troyes":"特鲁瓦", "Urawa Red Diamonds":"浦和红钻", "Vasco da Gama":"瓦斯科达伽马",
    "Viktoria Plzen":"比尔森胜利", "Virtus Entella":"恩特拉", "Vitória":"维多利亚", "Vitória de Guimaraes":"吉马良斯",
    "Västerås SK":"韦斯特罗斯", "Werder Bremen":"云达不来梅", "Çorum FK":"乔鲁姆", "Örgryte IS":"奥尔格里特",
    "Cong An Ha Noi":"河内公安", "Perth Glory FC":"珀斯光荣", "Adelaide United FC":"阿德莱德联", "FC Cologne":"科隆",
    "Newcastle Jets":"纽卡斯尔喷气机", "Shanghai Port FC":"上海海港", "Gamba Osaka":"大阪钢巴",
    "Central Coast Mariners FC":"中央海岸水手", "Macarthur FC":"麦克阿瑟", "Kyoto Sanga":"京都不死鸟",
    "Wellington Phoenix":"惠灵顿凤凰", "Vissel Kobe":"神户胜利船", "Beijing Guoan":"北京国安",
    "Kashiwa Reysol":"柏太阳神", "Daejeon Hana Citizen FC":"大田市民", "Port FC":"泰港",
    "Perth Glory":"珀斯光荣", "Melbourne Victory":"墨尔本胜利", "Adelaide United":"阿德莱德联",
    "Western Sydney Wanderers":"西悉尼流浪者", "Buriram United":"武里南联", "Pohang Steelers":"浦项制铁",
    "Jeonbuk Hyundai Motors":"全北现代", "Shanghai Shenhua":"上海申花", "Shandong Taishan":"山东泰山",
    "Tianjin Jinmen Tiger":"天津津门虎", "Qingdao West Coast":"青岛西海岸", "Liaoning Tieren":"辽宁铁人",
    "Henan FC":"河南队", "Chengdu Rongcheng":"成都蓉城", "Zhejiang Professional FC":"浙江队",
    "Mainz":"美因茨", "Mainz 05":"美因茨", "1. FC Köln":"科隆", "Hertha Berlin":"柏林赫塔",
    "Getafe":"赫塔费", "Real Sociedad":"皇家社会", "Celta Vigo":"塞尔塔", "Sevilla":"塞维利亚",
    "Konyaspor": "科尼亚体育", "Fenerbahce": "费内巴切", "Lille": "里尔",
    "Genclerbirligi": "根克勒比利吉", "Barcelona": "巴塞罗那",
    "Bayer Leverkusen": "勒沃库森", "SC Paderborn 07": "帕德博恩",
    "Borussia Mönchengladbach": "门兴格拉德巴赫", "Atlético Madrid": "马德里竞技",
    "Hamburg SV": "汉堡", "1. FC Magdeburg": "马格德堡", "AC Milan": "AC米兰",
    "ADO Den Haag": "海牙", "AEK Athens": "雅典AEK", "AFC Bournemouth": "伯恩茅斯",
    "AS Monaco": "摩纳哥", "AS Roma": "罗马", "Alanyaspor": "阿拉尼亚体育",
    "Alavés": "阿拉维斯", "Arsenal": "阿森纳", "Aston Villa": "阿斯顿维拉",
    "Atalanta": "亚特兰大", "Austria Lustenau": "奥地利卢斯特瑙",
    "Bayern Munich": "拜仁慕尼黑", "Bologna": "博洛尼亚", "Borussia Dortmund": "多特蒙德",
    "Brentford": "布伦特福德", "Brest": "布雷斯特", "Brighton & Hove Albion": "布莱顿",
    "Cagliari": "卡利亚里", "Chelsea": "切尔西", "Como": "科莫", "Coventry City": "考文垂",
    "Crystal Palace": "水晶宫", "Deportivo": "拉科鲁尼亚", "Elche": "埃尔切",
    "Espanyol": "西班牙人", "Everton": "埃弗顿", "FC Porto": "波尔图",
    "Feyenoord Rotterdam": "费耶诺德", "Fiorentina": "佛罗伦萨",
    "Fortuna Sittard": "福图纳锡塔德", "Frosinone": "弗罗西诺内", "Fulham": "富勒姆",
    "Galatasaray": "加拉塔萨雷", "Genoa": "热那亚", "Goztepe": "哥兹塔比",
    "Hamarkameratene": "汉坎", "Hull City": "赫尔城", "Internazionale": "国际米兰",
    "Ipswich Town": "伊普斯维奇", "Lazio": "拉齐奥", "Le Havre AC": "勒阿弗尔",
    "Leeds United": "利兹联", "Lens": "朗斯", "Levante": "莱万特", "Liverpool": "利物浦",
    "Lyon": "里昂", "Manacor": "马纳科尔", "Manchester City": "曼城",
    "Manchester United": "曼联", "Monza": "蒙扎", "NK Celje": "采列", "Napoli": "那不勒斯",
    "Newcastle United": "纽卡斯尔联", "PSV Eindhoven": "埃因霍温",
    "Paris Saint-Germain": "巴黎圣日耳曼", "RAAL La Louvière": "拉卢维耶尔",
    "RB Leipzig": "莱比锡", "RB Salzburg": "萨尔茨堡", "Real Betis": "皇家贝蒂斯",
    "Real Madrid": "皇家马德里", "Rot-Weiss Essen": "埃森", "SC Freiburg": "弗赖堡",
    "SV Elversberg": "埃尔弗斯贝格", "Schalke 04": "沙尔克04",
    "Shakhtar Donetsk": "顿涅茨克矿工", "Sint-Truidense": "圣图尔登",
    "Slavia Prague": "布拉格斯拉维亚", "Slovan Bratislava": "布拉迪斯拉发",
    "Sporting CP": "葡萄牙体育", "Strasbourg": "斯特拉斯堡", "Telstar": "特尔斯达",
    "Udinese": "乌迪内斯", "Valencia": "瓦伦西亚", "Venezia": "威尼斯",
    "VfB Stuttgart": "斯图加特", "Viking FK": "维京", "Villarreal": "比利亚雷亚尔",
    "Vålerenga": "瓦勒伦加", "WSG Swarovski Tirol": "蒂罗尔", "Willem II": "威廉二世",
    "Zulte-Waregem": "祖尔特瓦勒海姆", "Sassuolo": "萨索洛", "Parma": "帕尔马",
}


def key(name):
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", name)
                            if not unicodedata.combining(c)).casefold().split())


LOOKUP = {key(name): label for name, label in NAMES.items()}


def chinese_team(name):
    if not isinstance(name, str) or not name.strip():
        return "未知球队"
    if re.search(r"[\u4e00-\u9fff]", name):
        return name
    return LOOKUP.get(key(name), "球队译名待补充")


def localize_context(context):
    # Also supports cached contexts without modifying frozen prediction inputs.
    for field in ("recent", "venue_recent", "schedule_recent", "upcoming"):
        for rows in context.get(field) or []:
            for row in rows:
                original = row.get("opponent_original") or row.get("opponent", "")
                row.update(opponent=chinese_team(original), opponent_original=original)
    for row in context.get("h2h") or []:
        original = row.get("opponent_original") or row.get("opponent", "")
        row.update(opponent=chinese_team(original), opponent_original=original)
    return context
