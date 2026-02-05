import app  # noqa: F401
import app.__init__  # noqa: F401
import app.errors  # noqa: F401
import app.iso  # noqa: F401
import app.models  # noqa: F401
import app.routers.v1  # noqa: F401
import app.services  # noqa: F401
import app.utils  # noqa: F401


def test_api_module_imports() -> None:
    assert True
