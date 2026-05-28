import sqlite3
import os

# 프로젝트 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, "kschoolfood.db")
SCHEMA_PATH = os.path.join(BASE_DIR, "schema.sql")

def init_local_sqlite():
    print(f"Initializing local SQLite database: {DB_PATH}")
    print(f"Applying schema from: {SCHEMA_PATH}")
    
    # DB 연결
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    try:
        # schema.sql 읽기
        with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
            schema_sql = f.read()
            
        # SQL 스크립트 실행
        cursor.executescript(schema_sql)
        conn.commit()
        print("Local SQLite database initialized and seeded successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Error initializing database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    init_local_sqlite()
