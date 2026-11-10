"""Настройки сторожа: источники, веса, словари, эмитенты, пороги (план PLAN_NEWS_WATCH_0811 v2, «да» Олега 08.11)."""
from __future__ import annotations

import os
from pathlib import Path

STATE_DIR = Path(os.environ.get("NEWS_STATE_DIR", "/opt/news-watch/state"))
TG_TOKEN = os.environ.get("NEWS_TG_TOKEN", "")
TG_CHAT_ID = os.environ.get("NEWS_TG_CHAT_ID", "")

KEEP_DAYS = 7                         # «уже прислал» — 7 суток, потом вычищается
COIN_COOLDOWN_S = 6 * 3600            # по монете не чаще раза в 6 ч, кроме ухудшения
MUTE_ON_REFUSAL_S = 30 * 60           # 403 / 429 / 418 — источник молчит 30 мин
BROKEN_AFTER = 2                      # разбор пуст при живом ответе 2 обхода подряд — «перестал читаться»
MARKET_WIDE_COINS = 3                 # ≥ 3 монет с тревогой хода в одном обороте — одно сообщение «рынок»

# Минуты скана боя (:00–:02, :15–:17, :30–:32, :45–:47): к биржам и сайтам не ходим.
def quiet_minute(minute: int) -> bool:
    return minute % 15 <= 2


# Вес источника (план, п. 4).
W_OFFICIAL = 1.0      # объявление биржи; госорган США
W_MAJOR = 0.6         # CoinDesk, The Block
W_MEDIA = 0.4         # прочие СМИ

# name, kind (exchange | gov | media), parser, url, period_s, weight, min_interval_s (на домен)
SOURCES = [
    ("Bybit", "exchange", "bybit", "https://api.bybit.com/v5/announcements/index?locale=en-US&limit=20", 60, W_OFFICIAL),
    ("OKX", "exchange", "okx", "https://www.okx.com/api/v5/support/announcements", 60, W_OFFICIAL),
    ("Bitget", "exchange", "bitget", "https://api.bitget.com/api/v2/public/annoucements?language=en_US", 60, W_OFFICIAL),
    ("KuCoin", "exchange", "kucoin", "https://api.kucoin.com/api/v3/announcements?pageSize=20&lang=en_US", 60, W_OFFICIAL),
    ("SEC", "gov", "rss", "https://www.sec.gov/news/pressreleases.rss", 300, W_OFFICIAL),
    ("SEC иски", "gov", "rss", "https://www.sec.gov/enforcement-litigation/litigation-releases/rss", 300, W_OFFICIAL),
    ("DOJ", "gov", "rss", "https://www.justice.gov/news/rss", 300, W_OFFICIAL),
    ("FinCEN", "gov", "fincen", "https://www.fincen.gov/news", 600, W_OFFICIAL),
    ("OFAC", "gov", "ofac", "https://ofac.treasury.gov/recent-actions", 600, W_OFFICIAL),
    ("CFTC", "gov", "cftc", "https://www.cftc.gov/PressRoom/PressReleases", 600, W_OFFICIAL),
    ("CoinDesk", "media", "rss", "https://www.coindesk.com/arc/outboundfeeds/rss", 300, W_MAJOR),
    ("The Block", "media", "rss", "https://www.theblock.co/rss.xml", 300, W_MAJOR),
    ("Cointelegraph", "media", "rss", "https://cointelegraph.com/rss", 300, W_MEDIA),
    ("Decrypt", "media", "rss", "https://decrypt.co/feed", 300, W_MEDIA),
    ("CryptoSlate", "media", "rss", "https://cryptoslate.com/feed/", 300, W_MEDIA),
    ("Bitcoin Magazine", "media", "rss", "https://bitcoinmagazine.com/feed", 300, W_MEDIA),
    ("Bitcoinist", "media", "rss", "https://bitcoinist.com/feed/", 300, W_MEDIA),
]
# Запасной адрес источника: основной не ответил — читается этот (своё имя «… · страница»: первый обход — «видено» без отправки).
# SEC: лента пресс-релизов отдаёт голый 404 nginx с 10.11 ~17:30 UTC (сайт SEC на неё по-прежнему ссылается; соседние ленты
# SEC живы) — пресс-релизы выходят, читаются со страницы newsroom.
FALLBACKS = {"SEC": ("sec_html", "https://www.sec.gov/newsroom/press-releases")}

# Binance (поток объявлений с ключом или неофициальная страница) — по ответу Олега; пока не включён.
BINANCE_CMS = ("Binance", "exchange", "binance_cms",
               "https://www.binance.com/bapi/composite/v1/public/cms/article/list/query?type=1&pageNo=1&pageSize=20",
               60, W_OFFICIAL)
if os.environ.get("NEWS_BINANCE_CMS") == "1":
    SOURCES.append(BINANCE_CMS)

# Не чаще одного запроса к домену за столько секунд. Bybit — общий адрес с боем 2ч: бережём (план, п. 6).
DOMAIN_MIN_INTERVAL = {"api.bybit.com": 30.0}
DEFAULT_MIN_INTERVAL = 1.0

# Биржи, чей листинг — «🟢 крупное» (план, п. 4).
BIG_LISTING_EXCHANGES = {"Binance", "Bybit", "OKX", "Coinbase"}

# Монета → биржа-эмитент: новости о бирже = новости о монете (план, п. 5).
ISSUERS = {
    "MX": ("MEXC", ("MEXC", "MEXC Global")),
    "BNB": ("Binance", ("Binance",)),
    "OKB": ("OKX", ("OKX",)),
    "BGB": ("Bitget", ("Bitget",)),
    "KCS": ("KuCoin", ("KuCoin",)),
    "GT": ("Gate", ("Gate.io", "Gate.com")),
    "HTX": ("HTX", ("HTX", "Huobi")),
    "HT": ("HTX", ("HTX", "Huobi")),
    "CRO": ("Crypto.com", ("Crypto.com",)),
    "LEO": ("Bitfinex", ("Bitfinex", "iFinex")),
    "BMX": ("BitMart", ("BitMart",)),
}
# Токены бирж, о которых 🔴 шлём, даже если их нет в списке Олега (координатор 2123). HT — старое имя HTX.
ISSUER_TOKENS = {"MX", "BNB", "OKB", "BGB", "KCS", "GT", "HTX", "CRO", "LEO", "BMX"}
FORWARD_DAYS = (1, 3, 7)
# Беда САМОЙ биржи (а не её объявление о чужой монете): взлом, меры регулятора, уход из страны, остановка выводов.
# Только из чужих источников — в своей ленте биржа упоминает себя всегда («OKX will delist ADA» — не про OKB).
EXCHANGE_TROUBLE = [
    r"\bhack", r"exploit", r"drained", r"stolen", r"breach",
    r"sanction", r"\bcharge[sd]\b", r"indict", r"enforcement", r"special measure", r"money laundering", r"illicit",
    r"\bcuts?\b[^.]{0,40}?\boff\b", r"severs?\b", r"\bban(?:s|ned)?\b", r"lawsuit", r"\bsues?\b", r"investigat",
    r"\bfine[sd]?\b", r"settle", r"subpoena", r"arrest", r"seiz",
    r"\bexits?\b", r"pull(?:s|ing)? out of", r"cease[sd]? operations", r"halt\w* operations",
    r"(?:halt|suspend|pause|freez)\w* (?:all )?(?:user )?withdrawals", r"insolven", r"bankrupt",
]               # проверка вперёд: цены токена и BTC через 1 / 3 / 7 дней

# Тяжесть слов (план, п. 4). Регулярные выражения по заголовку, без регистра.
SEVERE = [   # 1.0
    r"delist", r"cease[sd]? trading", r"suspend\w* (?:\w+ )?(?:withdrawal|deposit|trading)", r"halt\w* (?:withdrawal|trading)",
    r"\bhack", r"exploit", r"drained", r"stolen", r"sanction", r"\bcharge[sd]\b", r"indict", r"enforcement action",
    r"special measure", r"money laundering", r"illicit", r"seiz", r"insolven", r"bankrupt", r"depeg",
    r"\bcuts?\b[^.]{0,40}?\boff\b", r"severs?\b", r"\bban(?:s|ned)?\b", r"arrest",
    r"\bexits?\b", r"pull(?:s|ing)? out of", r"cease[sd]? operations", r"halt\w* operations",
]
SERIOUS = [  # 0.7
    r"investigat", r"subpoena", r"lawsuit", r"\bsues?\b", r"monitoring tag", r"settle", r"\bfine[sd]?\b", r"warning",
    r"probe",
]
MINOR = [    # 0.4
    r"unlock", r"migration", r"swap deadline", r"network halt",
]
GOOD = [r"will list", r"\blists?\b", r"new listing", r"listing of", r"perpetual", r"launchpool", r"launchpad",
        r"futures will launch", r"margin will add"]

# Порог хода монеты (план, п. 3).
HIST_DAYS = 30
TAIL_Q = 0.002                         # хвост 0.2 %
FLOOR_MOVE = 0.02                      # не мельче 2 %
FLOOR_SIGMAS = 3.0
RED_FACTOR = 1.5                       # 🔴 — на половину дальше края хвоста
VOLUME_X = 3.0                         # 🟠 — объём ≥ 3× медианы окна
YOUNG_BARS = 7 * 96                    # меньше 7 дней истории — порог временный
YOUNG_FLOOR = 0.08                     # временный порог −8 % (за окно 1 ч и длиннее)
WINDOWS_MIN = (15, 60, 240)
