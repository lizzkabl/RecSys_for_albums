import os
import time
import argparse
import logging

import pandas as pd
from lyricsgenius import Genius
from tqdm import tqdm
import re
import unicodedata

GENIUS_TOKEN = "3a8YnV4wcAfo6jbpm6gHkAXfEQ9D9lOa8pZ28task2qRkOMWvFZGha_LJFnW6TPE"


def clean_lyrics(lyrics):
    """
    Убирает ненужный префикс до Read MoreВ и нормализует символы тире.
    Также удаляет языковые метки и пустые строки.
    """
    if not lyrics:
        return None

    # Отрезаем все до маркера Read MoreВ (включая маркер)
    #marker_pattern = r'Read\s*MoreВ'
    #parts = re.split(marker_pattern, lyrics, maxsplit=1)
    #if len(parts) > 1:
    #    lyrics = parts[1]

    read_more_match = re.search(r'.*?Read\s+More', lyrics, flags=re.IGNORECASE | re.DOTALL)
    if read_more_match:
        lyrics = lyrics[read_more_match.end():]

    # Нормализуем юникод и приводим разные виды тире к обычному дефису
    lyrics = unicodedata.normalize('NFKC', lyrics)
    lyrics = lyrics.replace('\u2013', '-')  # en dash
    lyrics = lyrics.replace('\u2014', '-')  # em dash
    lyrics = lyrics.replace('\u2015', '-')  # horizontal bar

    # Исправляем возможный мусорный эллипсис
    lyrics = lyrics.replace('вЂ¦', '...')  # исправление mojibake для '…'

    # Убираем строки с языковыми метками и пустые строки
    lines = [line for line in lyrics.strip().split('\n')
             if line.strip() and not any(lang in line.lower() for lang in [
                 'translations', 'contributors', 'lyrics', 't\u00fcrk\u00e7e',
                 'espa\u00f1ol', 'русский', 'portugu\u00eas', 'हिंदी',
                 'deutsch', 'fran\u00e7ais', 'italiano', 'polski',
                 'فارسی', '한국어', 'svenska'
             ])]
    cleaned = '\n'.join(lines).strip()

    return cleaned if cleaned else None

def is_translation(song):
    if not song:
        return False
    url = song.url.lower()
    title = song.title.lower()
    return (
        'translation' in url or
        'перевод' in title or
        'traduzione' in title or
        'traducción' in title
    )

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch song lyrics from Genius and attach them to a tracks dataset."
    )
    parser.add_argument(
        '--tracks-csv',
        required=True,
        help='Path to the CSV file containing track metadata (must include track_name and optionally album_id).'
    )
    parser.add_argument(
        '--albums-csv',
        required=False,
        help='Path to the albums CSV file (must include album_id and artist_name) if artist_name is not in tracks CSV.'
    )
    parser.add_argument(
        '--output-csv',
        required=True,
        help='Path to save the output CSV with lyrics.'
    )
    parser.add_argument(
        '--sleep',
        type=float,
        default=1.0,
        help='Seconds to wait between API calls to avoid rate limits.'
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=15,
        help='Timeout (seconds) for API requests.'
    )
    parser.add_argument(
        '--retries',
        type=int,
        default=3,
        help='Number of retries on request failures.'
    )
    return parser.parse_args()


def setup_logging():
    logging.basicConfig(
        format='%(asctime)s %(levelname)s: %(message)s',
        level=logging.INFO
    )


def load_data(tracks_path, albums_path=None):
    tracks = pd.read_csv(tracks_path)
    if albums_path:
        albums = pd.read_csv(albums_path)
        if 'artist_name' not in tracks.columns:
            tracks = tracks.merge(
                albums[['album_id', 'artist_name']],
                on='album_id',
                how='left'
            )
    if 'artist_name' not in tracks.columns:
        logging.error('artist_name column is missing and no albums file provided.')
        raise KeyError('artist_name column is required.')
    return tracks


def fetch_lyrics_for_tracks(tracks_df, genius_client, sleep_time, max_retries=3):
    lyrics = []

    for idx, row in tqdm(tracks_df.iterrows(), total=len(tracks_df), desc='Fetching lyrics'):
        orig_title = str(row.get('track_name', '')).strip()
        artist = str(row.get('artist_name', '')).strip()
        if not orig_title or not artist:
            lyrics.append(None)
            continue

        text = None
        attempt = 0

        while attempt < max_retries and text is None:
            try:
                song = genius_client.search_song(orig_title, artist)
                if song and not is_translation(song):
                    text = clean_lyrics(song.lyrics)
            except Exception as e:
                logging.warning(f"[{idx+1}] Попытка {attempt+1} не удалась для «{orig_title}»: {e}")
            attempt += 1
            time.sleep(sleep_time)

        # Повторяем с удалением (feat.) если основной вариант не сработал
        if text is None and '(feat.' in orig_title.lower():
            stripped_title = re.sub(r'\s*\(feat\..*?\)', '', orig_title, flags=re.IGNORECASE).strip()
            attempt = 0
            while attempt < max_retries and text is None:
                try:
                    song = genius_client.search_song(stripped_title, artist)
                    if song and not is_translation(song):
                        text = clean_lyrics(song.lyrics)
                except Exception as e:
                    logging.warning(f"[{idx+1}] Повтор {attempt+1} для stripped title «{stripped_title}»: {e}")
                attempt += 1
                time.sleep(sleep_time)

        lyrics.append(text)

    return lyrics



def main():
    args = parse_args()
    setup_logging()

    logging.info('Loading data...')
    tracks = load_data(args.tracks_csv, args.albums_csv)

    logging.info('Initializing Genius client...')
    genius = Genius(
        GENIUS_TOKEN,
        skip_non_songs=True,
        remove_section_headers=True,
        verbose=False
    )

    genius._session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    })

    logging.info(f'Start fetching lyrics for {len(tracks)} tracks...')
    #tracks['lyrics'] = fetch_lyrics_for_tracks(tracks, genius, args.sleep)
    tracks['lyrics'] = fetch_lyrics_for_tracks(tracks, genius, args.sleep, args.retries)

    tracks['has_lyrics'] = tracks['lyrics'].notnull() & (tracks['lyrics'].str.strip() != '')

    # 1) соберём статистику по паре (album_name, artist_name)
    album_stats = (
        tracks
        .groupby(['album_name', 'artist_name'])['has_lyrics']
        .agg(
            total_tracks='count',
            tracks_with_text='sum'
        )
        .assign(
            tracks_without_text=lambda df: df['total_tracks'] - df['tracks_with_text'],
            pct_without_text=lambda df: df['tracks_without_text'] / df['total_tracks']
        )
        .reset_index()
    )

    # 2) отбираем «хорошие» альбомы
    good = album_stats.loc[album_stats['pct_without_text'] <= 0.5, ['album_name', 'artist_name']]

    # 3) соединяем обратно с исходными треками — и остаются только нужные
    filtered_tracks = tracks.merge(
        good,
        on=['album_name', 'artist_name'],
        how='inner'
    )

    # 4) сохраняем
    filtered_tracks.to_csv(args.output_csv, index=False, encoding='utf-8-sig')

    logging.info(f'Saving results to {args.output_csv}')
    #tracks.to_csv(args.output_csv, index=False)
    #tracks.to_csv(args.output_csv, index=False, encoding='utf-8-sig')
    logging.info('Done.')


if __name__ == '__main__':
    main()
