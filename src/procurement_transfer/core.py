"""四份核心文件：来源片段、知识卡、题目、集中冻结/批次状态。"""
import copy
import json
import re
import unicodedata
from collections import Counter
from pathlib import Path

from .io import digest, file_hash, read_jsonl, write_json, write_jsonl

FILES = ("sources.jsonl", "knowledge.jsonl", "data.jsonl", "freeze.json")
OPS = {"explain", "identify", "apply", "correct", "combine"}
HUMAN_STATES = {"pending", "approved", "returned", "discarded"}


def content_hash(card):
    return digest({k: v for k, v in card.items() if k not in {"model_review", "human_review", "revision_history"}})


def load(root):
    root = Path(root)
    return (read_jsonl(root / FILES[0]), read_jsonl(root / FILES[1]), read_jsonl(root / FILES[2]),
            json.loads((root / FILES[3]).read_text(encoding="utf-8")))


def unique(rows, key):
    result = {r[key]: r for r in rows}
    if len(result) != len(rows):
        raise ValueError(f"重复编号：{key}")
    return result


def norm(text):
    return ''.join(c for c in unicodedata.normalize("NFKC", text).lower()
                   if not c.isspace() and not unicodedata.category(c).startswith("P"))


def validate_review(row):
    h, m = row["human_review"], row["model_review"]
    if h["status"] not in HUMAN_STATES or m["status"] not in {"pending", "passed", "needs_revision"}:
        raise ValueError("审核状态无效")
    if m["status"] != "pending":
        if not m["session_id"] or m["session_id"] == row["generation"]["session_id"]:
            raise ValueError("模型复核必须使用独立会话，不能冒充独立人工审核")
        if m["reviewed_hash"] != content_hash(row):
            raise ValueError("模型复核内容哈希已过期")
    if h["status"] != "pending":
        from datetime import datetime
        if not h["reviewer"] or h["reviewer"].lower() in {"ai", "model", "codex", "mock", "assistant"}:
            raise ValueError("需要真实人工审核人")
        if not h["date"] or datetime.fromisoformat(h["date"]).date().isoformat() != h["date"][:10]:
            raise ValueError("需要人工确认日期")
        if h["reviewed_hash"] != content_hash(row):
            raise ValueError("人工确认版本已过期")
    if h["status"] == "approved" and m["status"] != "passed":
        raise ValueError("应先完成独立模型复核，再由本人确认")


def validate_refs(refs, fragments):
    if not refs:
        raise ValueError("缺少原句依据")
    for ref in refs:
        source = fragments.get(ref["fragment_id"])
        if source is None or ref["quote"] not in source["raw_text"] or ref["locator"] != source["locator"]:
            raise ValueError("引用不存在、被改写或定位不匹配")
        if ref["text_sha256"] != source["text_sha256"]:
            raise ValueError("引用原文哈希过期")


def validate_knowledge(sources, cards, project=None):
    fragments = unique(sources, "fragment_id")
    unique(cards, "knowledge_id")
    checked = set()
    source_metadata = {}
    for source in sources:
        metadata = (source["file_path"], source["file_sha256"], source["title"], source["edition"])
        if source_metadata.setdefault(source["source_id"], metadata) != metadata:
            raise ValueError("同一来源的元数据/文件哈希不一致")
        if digest(source["raw_text"].encode("utf-8")) != source["text_sha256"]:
            raise ValueError("原文片段被改写")
        if not source["locator"].get("pdf_page") and not source["locator"].get("internal_file"):
            raise ValueError("缺少PDF或EPUB原文定位")
        if project and source["file_path"] not in checked:
            if file_hash(Path(project) / source["file_path"]) != source["file_sha256"]:
                raise ValueError("来源文件已变更，需重新提取并升版")
            checked.add(source["file_path"])
    names = set()
    for card in cards:
        required = {"knowledge_id", "revision", "type", "name", "content", "scope", "conditions", "exceptions", "formula_spec",
                    "evidence_refs", "supported_operations", "generation", "model_review", "human_review", "revision_history", "synthetic_demo"}
        if set(card) != required:
            raise ValueError(f"知识卡字段不完整或多余：{card.get('knowledge_id')}")
        if card["type"] not in {"concept", "rule", "metric", "process", "relation"} or type(card["revision"]) is not int or card["revision"] < 1:
            raise ValueError("知识类型或版本错误")
        if not card["content"] or not card["name"]:
            raise ValueError("知识内容为空")
        name = norm(card["name"])
        if name in names:
            raise ValueError("同名候选应先合并或明确口径")
        names.add(name)
        validate_refs(card["evidence_refs"], fragments)
        fields = {field for ref in card["evidence_refs"] for field in ref["fields"]}
        for field in ("content", "scope", "conditions", "exceptions", "formula_spec"):
            if card[field] is not None and field not in fields:
                raise ValueError(f"{card['knowledge_id']}:{field}缺原文支持")
        operations = card["supported_operations"]
        if not operations or any(o["operation"] not in OPS or not o["reason"] for o in operations):
            raise ValueError("操作范围无效")
        if len({o["operation"] for o in operations}) != len(operations):
            raise ValueError("重复操作")
        validate_review(card)
    return {"fragments": len(sources), "knowledge_cards": len(cards),
            "source_files": len({r["source_id"] for r in sources}),
            "model_passed": sum(c["model_review"]["status"] == "passed" for c in cards),
            "human_approved": sum(c["human_review"]["status"] == "approved" for c in cards),
            "types": dict(Counter(c["type"] for c in cards)),
            "supported_operations": dict(Counter(o["operation"] for c in cards for o in c["supported_operations"]))}


def approved(card):
    validate_review(card)
    return not card["synthetic_demo"] and card["human_review"]["status"] == "approved"


def human_decide(rows, key, row_id, decision, reviewer, date, notes):
    result = copy.deepcopy(rows)
    row = unique(result, key)[row_id]
    row["human_review"] = {"status": decision, "reviewer": reviewer, "date": date, "notes": notes, "reviewed_hash": content_hash(row)}
    validate_review(row)
    return result


def save_knowledge(root, cards, data, freeze):
    if freeze["status"] == "frozen":
        raise ValueError("已冻结数据不可原地修改，请复制为新版本")
    write_jsonl(Path(root) / "knowledge.jsonl", cards, replace=True)
    write_jsonl(Path(root) / "data.jsonl", data, replace=True)
    freeze["hashes"] = {}
    write_json(Path(root) / "freeze.json", freeze, replace=True)
