"""pytest config — adds the agent package to sys.path so tests can import it."""
import sys
from pathlib import Path

# services/agent/ — parent of tests/ — is the package root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
