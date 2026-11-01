import os
from pathlib import Path
import zipfile

IGNORED_PATTERNS = [
    ".env",
    ".env.local",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".git",
    "data/bizops.db",
    "test_agent.db",
    "test_e2e.db",
    "test_inventory.db",
    "credentials.json",
    "service-account.json",
]

def is_ignored(rel_path: str) -> bool:
    norm_path = rel_path.replace("\\", "/")
    for pattern in IGNORED_PATTERNS:
        if pattern.endswith(".db") and norm_path.endswith(".db"):
            return True
        if pattern in norm_path.split("/"):
            return True
        if norm_path == pattern or norm_path.startswith(pattern + "/"):
            return True
        if ".env" in norm_path and not norm_path.endswith(".env.example"):
            return True
        if "credentials" in norm_path.lower() and norm_path.endswith(".json"):
            return True
    return False

def create_safe_zip(output_zip: str = "bizops_agent_export.zip"):
    project_root = Path(__file__).resolve().parent.parent
    zip_path = project_root / output_zip
    count = 0

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(project_root):
            rel_dir = os.path.relpath(root, project_root)
            if rel_dir != "." and is_ignored(rel_dir):
                dirs[:] = []
                continue

            for file in files:
                rel_file = os.path.normpath(os.path.join(rel_dir, file)) if rel_dir != "." else file
                if rel_file == output_zip or is_ignored(rel_file):
                    continue

                full_path = os.path.join(root, file)
                zf.write(full_path, rel_file)
                count += 1

    print(f"Created secure export archive: {zip_path} ({count} files included, secrets excluded)")
    return zip_path

if __name__ == "__main__":
    create_safe_zip()

