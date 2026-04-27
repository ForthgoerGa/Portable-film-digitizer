import uvicorn
from server import app
import runtime_config

if __name__ == "__main__":
    print(
        "Film Digitizer PC server + Web UI starting at "
        f"http://{runtime_config.PC_SERVER_HOST}:{runtime_config.PC_SERVER_PORT}"
    )
    uvicorn.run(
        "server:app",
        host=runtime_config.PC_SERVER_HOST,
        port=runtime_config.PC_SERVER_PORT,
        reload=True,
        access_log=False,
    )
