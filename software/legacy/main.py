import uvicorn
from server import app

if __name__ == "__main__":
    print("Film Digitizer API server + Web UI starting at http://localhost:8000")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True, access_log=False)
