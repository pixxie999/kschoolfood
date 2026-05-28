try:
    from workers import WorkerEntrypoint
    IS_WORKERS = True
except ImportError:
    IS_WORKERS = False

if IS_WORKERS:
    class Default(WorkerEntrypoint):
        async def fetch(self, request, env=None):
            # Cloudflare Worker의 엄격한 Startup CPU Limit(1000ms)을 우회하기 위해
            # 무거운 모듈(FastAPI, Pydantic 등)을 이벤트 핸들러(fetch) 내부에서 지연 로딩(Lazy Loading)합니다.
            import asgi
            from app.main import app
            return await asgi.fetch(app, request, env)
else:
    # 로컬 개발 환경용 (uvicorn main:app)
    from app.main import app
