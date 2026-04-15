# Testing Conventions

## Intent & Scope

Test quality rules for `tests/`. See `sdd/DESIGN.md` for code style.

## Rules

1. **Every test must have at least one meaningful assertion** -- "no crash" is not a test.
   Failure paths need `pytest.raises` with a `match=` pattern.

2. **Assert behavior, not types** -- `isinstance` may accompany behavioral assertions
   but never as the sole check.

3. **Prefer real objects over mocks** -- use Pydantic models, in-memory SQLite, or
   `remote-store` `MemoryBackend` before reaching for mocks.

4. **Parametrize over 3+ repetitive cases** with `@pytest.mark.parametrize`.

5. **Tests must survive refactoring** -- if renaming a private attribute breaks the test,
   the test is wrong.

## Test categories

### Unit tests (`tests/unit/`)

Cover pure logic only -- no I/O, no network, no Docker required.
Run with `hatch run test`.

Targets:
- `Envelope.make_idempotency_key` -- format, segments, custom schema version
- `Envelope.advance()` -- step promotion, parent linkage, immutability
- `Envelope` validation -- field constraints
- Any other pure functions in `shared/`

### Integration tests (`tests/integration/`)

Require Docker services (`docker compose up -d`). Mark with `@pytest.mark.integration`.
Not run by default.

```bash
pytest -m integration
```
