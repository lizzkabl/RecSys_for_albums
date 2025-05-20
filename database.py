import mysql.connector
import pandas as pd
import numpy as np

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.neighbors import NearestNeighbors
from scipy.sparse import hstack, csr_matrix
from sklearn.preprocessing import StandardScaler

# ---------- Настройка подключения к MySQL ----------
conn = mysql.connector.connect(
    host='localhost',
    user='user',
    password='qwertylkjh',
    database='music_db'
)
cursor = conn.cursor()

# ---------- Создание таблиц, если их нет ----------
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INT PRIMARY KEY,
        username VARCHAR(255) UNIQUE NOT NULL
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS albums (
        album_id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(255) NOT NULL,
        artist VARCHAR(255) NOT NULL,
        album_name VARCHAR(255) UNIQUE NOT NULL
    )
''')

cursor.execute('''
    CREATE TABLE IF NOT EXISTS ratings (
        rating_id INT AUTO_INCREMENT PRIMARY KEY,
        user_id INT,
        album_id INT,
        rating INT CHECK (rating BETWEEN 1 AND 5),
        UNIQUE(user_id, album_id),
        FOREIGN KEY (user_id) REFERENCES users(user_id),
        FOREIGN KEY (album_id) REFERENCES albums(album_id)
    )
''')
conn.commit()
print("✅ Таблицы успешно созданы или уже существуют.")

# ---------- Загрузка CSV и заполнение albums ----------
_df = pd.read_csv('spotify_albums.csv')
if not _df.empty:
    rows = [
        (row['album_name'], row['artist_name'], row['album_name'])
        for _, row in _df.iterrows()
    ]
    cursor.executemany(
        "INSERT IGNORE INTO albums (title, artist, album_name) VALUES (%s, %s, %s)",
        rows
    )
    conn.commit()
    print("✅ Загружены данные об альбомах из CSV.")

# ---------- Система рекомендаций ----------
def init_recommendation_system():
    _df['text_features'] = (
        _df['genres'] + ' ' + _df['artist_name'] + ' ' +
        _df['album_type'] + ' ' + _df['label']
    )
    vectorizer = TfidfVectorizer()
    text_matrix = vectorizer.fit_transform(_df['text_features'])

    scaler = StandardScaler()
    numeric = scaler.fit_transform(_df[
        ['total_duration_min', 'artist_popularity', 'total_tracks',
         'artist_followers', 'album_popularity']
    ])
    features = csr_matrix(hstack([text_matrix, numeric]))
    dense = features.toarray()

    knn = NearestNeighbors(n_neighbors=50, metric='cosine')
    knn.fit(features)
    return knn, dense

knn_model, dense_features = init_recommendation_system()

# ---------- Класс профиля пользователя ----------
class UserProfile:
    def __init__(self, user_id: int, username: str):
        self.user_id = user_id
        self.username = username
        self.rated: dict[str, int] = {}
        self.profile: np.ndarray | None = None
        self._register_user()
        self._load_from_db()

    def _register_user(self):
        cursor.execute(
            "INSERT IGNORE INTO users (user_id, username) VALUES (%s, %s)",
            (self.user_id, self.username)
        )
        conn.commit()
        print(f"👤 Зарегистрирован пользователь: ID={self.user_id}, username='{self.username}'")

    def _load_from_db(self):
        query = '''
            SELECT a.album_name, r.rating
            FROM ratings r
            JOIN albums a ON r.album_id = a.album_id
            WHERE r.user_id = %s
        '''
        cursor.execute(query, (self.user_id,))
        for album_name, rating in cursor.fetchall():
            self.rated[album_name] = rating
        self._update_profile()
        print(f"📥 Загрузка рейтингов пользователя {self.user_id} завершена.")

    def add_rating(self, album_name: str, rating: int) -> bool:
        cursor.execute(
            "SELECT album_id FROM albums WHERE album_name = %s",
            (album_name,)
        )
        row = cursor.fetchone()
        if row is None:
            print(f"⚠️ Альбом '{album_name}' не найден.")
            return False

        album_id = row[0]
        cursor.execute(
            '''
            INSERT INTO ratings(user_id, album_id, rating)
            VALUES(%s, %s, %s)
            ON DUPLICATE KEY UPDATE rating = VALUES(rating)
            ''',
            (self.user_id, album_id, rating)
        )
        conn.commit()
        self.rated[album_name] = rating
        self._update_profile()
        print(f"⭐ Поставлена оценка {rating} альбому '{album_name}' пользователем {self.user_id}")
        return True

    def _update_profile(self):
        if not self.rated:
            self.profile = None
            return
        weights = np.zeros(dense_features.shape[1])
        total = 0
        for name, rate in self.rated.items():
            idx = _df.index[_df['album_name'] == name][0]
            weights += dense_features[idx] * rate
            total += rate
        self.profile = weights / total if total > 0 else None

    def get_recommendation(self) -> str | None:
        if self.profile is None:
            return None
        dists, inds = knn_model.kneighbors([self.profile])
        for i in inds[0]:
            name = _df.iloc[i]['album_name']
            if name not in self.rated:
                print(f"🎧 Рекомендован альбом: {name}")
                return name
        return None
