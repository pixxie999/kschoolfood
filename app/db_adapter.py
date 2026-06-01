import sqlite3
import os
import logging
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

# 프로젝트 루트 디렉터리에 SQLite DB 파일 경로 설정
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCAL_DB_PATH = os.path.join(BASE_DIR, "kschoolfood.db")

class LocalSQLiteAdapter:
    """
    로컬 Python 단독 스크립트 실행 또는 로컬 테스트 환경용 SQLite 어댑터
    """
    def __init__(self, db_path: str = LOCAL_DB_PATH):
        self.db_path = db_path
        # 데이터베이스 파일이 없는 경우, 테이블 생성은 외부 스크립트(schema.sql 등)로 처리하되
        # 파일만 먼저 생성 가능하도록 함
        if not os.path.exists(self.db_path):
            conn = sqlite3.connect(self.db_path)
            conn.close()

    def _get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    async def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        except Exception as e:
            logger.error(f"Local SQLite fetch_all error: {e}")
            return []
        finally:
            conn.close()

    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            row = cursor.fetchone()
            return dict(row) if row else None
        except Exception as e:
            logger.error(f"Local SQLite fetch_one error: {e}")
            return None
        finally:
            conn.close()

    async def execute(self, query: str, params: tuple = ()) -> bool:
        conn = self._get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            conn.commit()
            return True
        except Exception as e:
            conn.rollback()
            logger.error(f"Local SQLite execute error: {e}")
            raise e
        finally:
            conn.close()


class D1Adapter:
    """
    Cloudflare Workers 환경에서 env.DB 바인딩을 활용하는 D1 데이터베이스 어댑터
    """
    def __init__(self, d1_binding):
        self.db = d1_binding

    async def fetch_all(self, query: str, params: tuple = ()) -> List[Dict[str, Any]]:
        stmt = self.db.prepare(query)
        if params:
            stmt = stmt.bind(*params)
        result = await stmt.all()
        
        # JS Proxy 객체 또는 딕셔너리에서 'results' 데이터 추출
        if hasattr(result, "results"):
            return list(result.results)
        elif isinstance(result, dict) and "results" in result:
            return result["results"]
        return []

    async def fetch_one(self, query: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
        stmt = self.db.prepare(query)
        if params:
            stmt = stmt.bind(*params)
        result = await stmt.all()
        
        results = []
        if hasattr(result, "results"):
            results = list(result.results)
        elif isinstance(result, dict) and "results" in result:
            results = result["results"]
            
        return results[0] if results else None

    async def execute(self, query: str, params: tuple = ()) -> bool:
        stmt = self.db.prepare(query)
        if params:
            stmt = stmt.bind(*params)
        await stmt.run()
        return True


def get_db_adapter(env=None) -> Union[D1Adapter, LocalSQLiteAdapter]:
    try:
        if env is not None:
            db = env.DB
            if db is not None:
                return D1Adapter(db)
    except Exception:
        pass
    return LocalSQLiteAdapter()
