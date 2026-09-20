import os

import uvicorn
from dotenv import load_dotenv


def main() -> None:
    load_dotenv()
    port = int(os.environ.get("FAKE_APP_PORT", "5055"))
    uvicorn.run("cua.fake_app.main:app", host="127.0.0.1", port=port, reload=False)


if __name__ == "__main__":
    main()
