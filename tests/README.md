# tests

pytest suite. Unit tests are hermetic: no Docker, no network.
Tests that need the running stack are marked `@pytest.mark.stack`.
