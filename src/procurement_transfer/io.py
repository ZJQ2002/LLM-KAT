import hashlib
import json
from pathlib import Path


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256((value if isinstance(value, bytes) else canonical(value).encode("utf-8"))).hexdigest()


def file_hash(path):
    return digest(Path(path).read_bytes())


def read_jsonl(path, *, sealed=False):
    path = Path(path)
    if not sealed and "sealed_test" in path.resolve().parts:
        raise ValueError("开发命令禁止读取 sealed_test；仅专用测试构建/审核/冻结流程可访问")
    if not path.exists():
        raise FileNotFoundError(path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]


def write_text(path, text, *, replace=False):
    path = Path(path)
    if path.exists():
        if path.read_bytes() == text.encode("utf-8"):
            return
        if not replace:
            raise ValueError(f"拒绝覆盖已有产物：{path}；请使用新输出目录或显式修订流程")
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(text, encoding="utf-8", newline="")
    temp.replace(path)


def write_json(path, value, **kwargs):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", **kwargs)


def write_jsonl(path, rows, **kwargs):
    write_text(path, "".join(canonical(row) + "\n" for row in rows), **kwargs)

