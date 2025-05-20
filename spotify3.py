import time
import re
import unicodedata
import logging
import csv

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from requests.exceptions import ConnectionError

# === Credentials ===
SPOTIPY_CLIENT_ID     = "ef2dd8c50e474c05b4f0d1c4f65fc412"
SPOTIPY_CLIENT_SECRET = "9b7781327c10494fa123e5de8965270f"

# === Parameters ===
NEW_RELEASES_LIMIT   = 50    # сколько «новых релизов» взять
MIN_ALBUM_POPULARITY = 20    # мин. популярность альбома
SLEEP_TIME           = 0.2   # задержка между запросами

# Жанры + (artist_limit, albums_per_artist)
GENRES_CONFIG = {
    "pop":        (20, 2),
    "rock":       (15, 2),
    "jazz":       (10, 2),
    "hip hop":    (50, 2),
    "classical":  (5, 2),
    "electronic": (10, 2),
    #"folk":       (5,  1),
    "latin":      (8,  1),
    "edm":        (5, 2),
    "deep-house":        (5,  2),
    "detroit-techno":    (5,  2),
    "funk":       (5, 2),
    "emo":       (10,  1),
    "punk":      (5,  2),
    "r-n-b":        (15, 2),
    "afrobeat":       (5,  2),
    "garage":       (5,  2),
    "metal":      (5,  2),
    "alternative":        (5,  2),
    "indie":      (5,  2),
    "indie-pop":        (5,  2),
    "k-pop":       (5,  2),
    "soul":       (5,  2),
    "singer-songwriter":      (5,  2)

}

# === Setup logging ===
logging.basicConfig(format='%(asctime)s %(levelname)s: %(message)s',
                    level=logging.INFO)

# === Spotify client ===
sp = spotipy.Spotify(
    client_credentials_manager=SpotifyClientCredentials(
        client_id=SPOTIPY_CLIENT_ID,
        client_secret=SPOTIPY_CLIENT_SECRET
    ),
    requests_timeout=30
)

# === Helper for Unicode normalization ===
def normalize_str(s: str) -> str:
    """Нормализует строку в NFC для корректной записи любых юникод-символов."""
    if not isinstance(s, str):
        return s
    # NFC сохраняет символы вроде ударений, умляутов, волн и т.п.
    return unicodedata.normalize('NFC', s)


def clean_track_name(name: str) -> str:
    txt = normalize_str(name).replace('…', '...')
    m = re.match(r'(.+?)\s+with\s+(.+)', txt, flags=re.IGNORECASE)
    if m:
        return f"{m.group(1).strip()} (feat. {m.group(2).strip()})"
    return re.sub(r'\(with\s+', '(feat. ', txt, flags=re.IGNORECASE)


def safe_track_fetch(track_id, retries=3, delay=1):
    for _ in range(retries):
        try:
            return sp.track(track_id)
        except Exception as e:
            logging.warning(f"fetch track {track_id} failed ({e}), retry in {delay}s")
            time.sleep(delay)
    logging.error(f"Could not fetch track {track_id}")
    return None

# 1) Собираем все возможные album_id
album_ids = set()

#logging.info("Fetching last releases...")
#releases = sp.new_releases(limit=NEW_RELEASES_LIMIT)['albums']['items']
#time.sleep(SLEEP_TIME)
#for rel in releases:
#   album_ids.add(rel['id'])
#logging.info(f"Из новейших релизов: собрано {len(releases)} ID")

# 1b) по жанрам
for genre, (artist_lim, alb_lim) in GENRES_CONFIG.items():
    logging.info(f"Genre «{genre}»: search top {artist_lim} artists, {alb_lim} albums each")
    arts = sp.search(q=f"genre:{genre}", type="artist", limit=artist_lim)['artists']['items']
    time.sleep(SLEEP_TIME)
    for art in arts:
        try:
            alb_list = sp.artist_albums(art['id'], album_type='album', limit=alb_lim)['items']
            time.sleep(SLEEP_TIME)
        except ConnectionError:
            logging.warning(f"Failed to fetch albums for artist {art['name']}")
            continue
        for alb in alb_list:
            album_ids.add(alb['id'])
logging.info(f"Всего уникальных альбомов после жанровой выборки: {len(album_ids)}")

# 2) Строим и фильтруем таблицу альбомов
albums_data = []
for aid in album_ids:
    try:
        a = sp.album(aid)
        time.sleep(SLEEP_TIME)
    except ConnectionError:
        logging.warning(f"Cannot load album {aid}")
        continue
    pop = a.get('popularity', 0)
    if pop <= MIN_ALBUM_POPULARITY:
        continue
    art = a['artists'][0]
    try:
        art_info = sp.artist(art['id'])
        time.sleep(SLEEP_TIME)
    except ConnectionError:
        logging.warning(f"Cannot load artist {art['name']}")
        continue

    genres_val = ", ".join(art_info['genres']) if art_info['genres'] else 'Unknown'
    total_dur = sum(t['duration_ms'] for t in a['tracks']['items']) / 60000
    albums_data.append({
        'album_id':           aid,
        'album_name':         normalize_str(a['name']),
        'artist_name':        normalize_str(art['name']),
        'genres':             normalize_str(genres_val),
        'release_date':       a['release_date'],
        'total_duration_min': round(total_dur, 2),
        'total_tracks':       a['total_tracks'],
        'artist_popularity':  art_info['popularity'],
        'album_popularity':   pop,
        'album_type':         a['album_type'],
        'featured_artists':   normalize_str(", ".join(x['name'] for x in a['artists'][1:])),
        'artist_followers':   art_info['followers']['total'],
        'label':              normalize_str(a.get('label', 'Unknown'))
    })
logging.info(f"После фильтра по популярности > {MIN_ALBUM_POPULARITY}: {len(albums_data)} альбомов")

# Сохраняем albums CSV с BOM для Excel и utf-8
with open('spotify_albums1.csv', 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    header = list(albums_data[0].keys())
    writer.writerow(header)
    for row in albums_data:
        writer.writerow([row[h] for h in header])
logging.info("spotify_albums1.csv создан")

# 3) Строим таблицу треков
tracks_data = []
for alb in albums_data:
    aid = alb['album_id']
    try:
        tracks = sp.album_tracks(aid)['items']
        time.sleep(SLEEP_TIME)
    except ConnectionError:
        logging.warning(f"Cannot fetch tracks for album {aid}")
        continue
    for t in tracks:
        det = safe_track_fetch(t['id'])
        time.sleep(SLEEP_TIME)
        tracks_data.append({
            'album_id':        alb['album_id'],
            'track_name':      normalize_str(clean_track_name(t['name'])),
            'album_name':      alb['album_name'],
            'artist_name':     alb['artist_name'],
            'featured_artists': normalize_str(", ".join(x['name'] for x in t['artists'][1:])),
            'track_number':    t['track_number'],
            'duration_ms':     t['duration_ms'],
            'explicit':        t['explicit'],
            'popularity':      det['popularity'] if det else 0
        })
logging.info(f"Всего треков для сохранения: {len(tracks_data)}")

# Сохраняем tracks CSV
with open('spotify_tracks1.csv', 'w', encoding='utf-8-sig', newline='') as f:
    writer = csv.writer(f)
    header = list(tracks_data[0].keys())
    writer.writerow(header)
    for row in tracks_data:
        writer.writerow([row[h] for h in header])
logging.info("spotify_tracks1.csv создан")
