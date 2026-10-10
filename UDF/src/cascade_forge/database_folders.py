"""Named user databases and conservative closed-contour DAT ingestion."""
from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from .profile_library import ProfileLibrary, coordinate_text

DEFAULT_DATABASE = "默认数据库"


def validate_name(name: str) -> str:
    name = name.strip()
    if (not name or len(name) > 80 or name in {".", ".."}
            or re.search(r'[<>:"/\\|?*\x00-\x1f]', name) or name.endswith((".", " "))
            or name.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL",
                                               *(f"COM{i}" for i in range(1, 10)),
                                               *(f"LPT{i}" for i in range(1, 10))}):
        raise ValueError("数据库名称不可为空或包含路径分隔符、保留名称等非法字符")
    return name


class DatabaseFolders:
    def __init__(self, root: Path, legacy: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.legacy = legacy
        self.library(DEFAULT_DATABASE, create=True)

    def library(self, name: str, *, create: bool = False) -> ProfileLibrary:
        name = validate_name(name)
        if create and name == "历史生成数据":
            raise ValueError("此名称保留给历史数据，请使用其他数据库名称")
        if name == "历史生成数据" and (self.legacy / "profiles.sqlite3").is_file():
            return ProfileLibrary(self.legacy)
        path = (self.root / name).resolve()
        if path.parent != self.root:
            raise ValueError("数据库目录必须位于 databases 根目录内")
        if not path.is_dir() and not create:
            raise ValueError("数据库不存在，请先创建")
        return ProfileLibrary(path)

    def list(self) -> list[dict[str, str]]:
        result = [{"name": path.name, "path": str(path.resolve())}
                  for path in sorted(self.root.iterdir())
                  if path.is_dir() and not path.is_symlink()
                  and path.resolve().parent == self.root]
        if (self.legacy / "profiles.sqlite3").is_file():
            result.append({"name": "历史生成数据", "path": str(self.legacy.resolve())})
        return result


def parse_dat(path: Path) -> dict[str, Any]:
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("DAT 文件超过 4 MB，请减少采样点")
    data = path.read_bytes()
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("gb18030")
    points = []
    xyz = False
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith(("#", "!", ";")):
            continue
        if line.upper() == "XYZ":
            xyz = True
            continue
        parts = line.replace(",", " ").split()
        try:
            values = [float(part.replace("D", "E").replace("d", "e")) for part in parts]
        except ValueError:
            if points:
                raise ValueError(f"第 {number} 行含无法识别的坐标") from None
            continue  # optional leading title/header
        if len(values) == 1 and not points and xyz:
            continue  # IGG XYZ declared point count
        if len(values) not in (2, 3):
            raise ValueError(f"第 {number} 行不是两列 XY 或三列 XYZ 坐标")
        if len(values) == 3 and abs(values[2]) > 1e-12:
            raise ValueError("仅支持二维截面 DAT，Z 坐标必须为 0")
        points.append(values[:2])
    coordinates = np.asarray(points, dtype=float)
    if len(coordinates) < 7 or not np.isfinite(coordinates).all():
        raise ValueError("DAT 需要至少 7 个有限坐标点")
    # Supported order: TE -> upper -> LE -> lower -> TE. Do not rotate or scale input.
    leading = int(np.argmin(coordinates[:, 0]))
    extent = float(np.ptp(coordinates[:, 0]))
    if extent <= 1e-12 or leading in (0, len(coordinates) - 1):
        raise ValueError("需要从尾缘经前缘返回尾缘的完整轮廓，不支持单侧曲线")
    if leading >= len(coordinates) - 2:
        raise ValueError("前缘两侧都需要至少三个采样点")
    side_a = coordinates[:leading + 1][::-1]
    side_b = coordinates[leading:]
    tolerance = extent * 0.08
    if min(side_a[-1, 0], side_b[-1, 0]) < np.max(coordinates[:, 0]) - tolerance:
        raise ValueError("两侧曲线没有在尾缘结束，请检查 DAT 点序")
    if len(side_a) < 3 or len(side_b) < 3:
        raise ValueError("前缘两侧都需要至少三个采样点")
    # Keep every original point, including an explicit finite-TE closing connector.
    # Imported DAT is a sampled outline, not an inferred native thickness model.
    if float(np.mean(side_a[:, 1])) < float(np.mean(side_b[:, 1])):
        side_a, side_b = side_b, side_a
    identifier = "dat_" + hashlib.sha256(data).hexdigest()[:24]
    return {"schema_version": "native-profile-1.0", "geometry_id": identifier,
            "family": "imported", "name": path.stem, "spec": {},
            "suction": side_a.tolist(), "pressure": side_b.tolist(), "camber": [],
            "coordinate_convention": "input-xy-preserved",
            "source_file": path.name, "source_sha256": hashlib.sha256(data).hexdigest(),
            "native_geometry": {"source_file": path.name, "points": coordinates.tolist()},
            "created_at": datetime.now(UTC).isoformat()}


def scan_dat(library: ProfileLibrary) -> dict[str, Any]:
    imported, skipped, errors = [], [], []
    for path in sorted(library.root.iterdir()):
        if path.suffix.lower() != ".dat" or not path.is_file():
            continue
        if path.is_symlink() or path.resolve().parent != library.root.resolve():
            errors.append({"file": path.name, "error": "不读取数据库目录外的链接文件"})
            continue
        try:
            existing = library.get(path.stem)
            if (existing and path.read_text(encoding="utf-8-sig")
                    == coordinate_text(existing, "dat")):
                skipped.append(path.name)
                continue
            record = parse_dat(path)
            library.save(record, write_files=False)
            imported.append(record["geometry_id"])
        except (ValueError, OSError, UnicodeError) as exc:
            errors.append({"file": path.name, "error": str(exc)})
    return {"imported": imported, "skipped": skipped, "errors": errors}
