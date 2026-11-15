from fastapi.testclient import TestClient

from palladion.api.app import create_app
from palladion.config import Settings


def test_the_desk_needs_the_key_and_the_live_page_does_not(tmp_path):
    s = Settings(room_key="k3y", case_worker=False, supervisor_agent=False, llm_provider="replay",
                 llm_cache_dir=tmp_path)
    with TestClient(create_app(s), base_url="https://testserver") as c:
        assert c.get("/api/scenario").status_code == 401
        assert c.get("/api/live").status_code != 401
        assert c.get("/", follow_redirects=False).status_code == 401
        r = c.get("/?key=k3y", follow_redirects=False)
        assert r.status_code == 303 and "palladion_room" in r.headers["set-cookie"]
        assert c.get("/api/scenario").status_code == 200
