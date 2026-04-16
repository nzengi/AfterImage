# Contributing to AFTERIMAGE

Thank you for your interest in improving AFTERIMAGE.

## License agreement

By submitting a pull request you agree that your contribution will be
licensed under the same terms as the project: **AGPL-3.0-or-later** for the
open-source distribution, and automatically included in commercial releases
under the project's dual-license model.

## Development setup

```bash
# Clone and enter the repository
git clone https://github.com/nzengi/AfterImage.git
cd afterimage

# Create a virtual environment
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate

# Install in editable mode with dev dependencies
pip install -e ".[dev,pyzbar]"
```

## Running the tests

```bash
# Fast tests only (no camera, no slow fountain tests)
pytest -m "not slow and not camera"

# All tests including slow fountain-code stress tests
pytest

# With coverage report
pytest --cov=afterimage --cov-report=term-missing
```

## Code style

This project uses `ruff` for linting and formatting, and `mypy` for type
checking.

```bash
# Lint and auto-fix
ruff check --fix afterimage/ tests/

# Format
ruff format afterimage/ tests/

# Type check
mypy afterimage/
```

All checks must pass before a pull request will be merged.

## Commit conventions

Use the [Conventional Commits](https://www.conventionalcommits.org/) format:

```
feat: add multi-file batch transmission
fix: correct LT decoder off-by-one in seed replay
security: increase PBKDF2 iterations to 800 000
perf: vectorise XOR step in LTEncoder
docs: add threat model section to SECURITY.md
test: add 50 % loss rate fountain stress test
```

## Security issues

Do **not** open a public issue for security vulnerabilities.
See [SECURITY.md](SECURITY.md) for the responsible disclosure process.

## Areas where contributions are most welcome

- Performance: GPU-accelerated QR generation, faster belief propagation
- Robustness: adaptive FPS, automatic exposure tuning for the camera
- Portability: Windows camera backend improvements, ARM/mobile testing
- Testing: hardware-in-the-loop tests, more loss-rate edge cases
- Documentation: tutorials, deployment guides, threat model elaboration