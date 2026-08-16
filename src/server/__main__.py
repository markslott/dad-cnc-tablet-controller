import uvicorn

from src.server.app import app
from src.server.config import load_settings


def main() -> None:
    settings = load_settings()
    uvicorn.run(app, host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
