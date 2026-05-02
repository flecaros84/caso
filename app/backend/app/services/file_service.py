from pathlib import Path
from app.config import settings
from app.models.schemas import FileItem


class FileService:
    def __init__(self) -> None:
        self.announcements_dir = settings.resolved_announcements_dir
        self.cv_dir = settings.resolved_cv_dir

    def list_announcements(self) -> list[FileItem]:
        return self._list_files(self.announcements_dir, "announcement", [".png", ".jpg", ".jpeg", ".webp", ".pdf", ".txt", ".md"])

    def list_cvs(self) -> list[FileItem]:
        return self._list_files(self.cv_dir, "cv", [".pdf", ".txt", ".md", ".docx"])

    def resolve_announcement(self, file_id: str) -> Path:
        return self._resolve_by_id(file_id, self.list_announcements())

    def resolve_cv(self, file_id: str) -> Path:
        return self._resolve_by_id(file_id, self.list_cvs())

    def _list_files(self, directory: Path, kind: str, suffixes: list[str]) -> list[FileItem]:
        if not directory.exists():
            return []
        files: list[FileItem] = []
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.suffix.lower() in suffixes:
                files.append(FileItem(
                    id=self._safe_id(path),
                    filename=path.name,
                    path=str(path),
                    kind=kind,
                ))
        return files

    def _resolve_by_id(self, file_id: str, files: list[FileItem]) -> Path:
        for item in files:
            if item.id == file_id:
                return Path(item.path)
        raise FileNotFoundError(f"No se encontró el archivo con id: {file_id}")

    @staticmethod
    def _safe_id(path: Path) -> str:
        return path.stem.lower().replace(" ", "_").replace(".", "_")
