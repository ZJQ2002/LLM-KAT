"""简化版数据阶段命令行。只有四份核心数据，不创建重复审核台账。"""
import argparse
import copy
import json
import sys
from pathlib import Path

import yaml

from .core import (approved, content_hash, human_decide, load, save_knowledge, unique,
                   validate_knowledge, validate_review)
from .dataset import (assign_cases, check_questions, duplicate_pairs, export_groups,
                      sample_batch, training_ready)
from .io import digest, file_hash, read_jsonl, write_json, write_jsonl


def config(path):
    path = Path(path).resolve()
    c = yaml.safe_load(path.read_text(encoding="utf-8"))
    project = (path.parent / c["project_root"]).resolve()
    return c, project, project / c["workspace"]


def parser():
    p = argparse.ArgumentParser(description="简化数据工程：sources / knowledge / data / freeze")
    sub = p.add_subparsers(dest="command", required=True)
    names = {"doctor": "核对启用/延期来源", "validate": "核验原文、知识卡和开发题目", "review-view": "生成并排只读知识卡页面",
             "model-review": "导入独立会话逐卡/逐题复核（不冒充人工）", "review-knowledge": "本人确认、退回或弃用知识卡",
             "revise-knowledge": "升版修订知识卡并标记受影响题目", "plan-cases": "分配单一case_family_id蓝图",
             "prepare-generation": "仅导出指定集合的蓝图和已审知识，不读测试题", "import-data": "导入模型一次生成的题目与双目标",
             "review-data": "本人确认一题；评价组需全部三题确认", "sample-batch": "首批全审/高风险全审/后续分层抽样",
             "confirm-batch": "本人确认批次抽查结果", "dedup": "开发题面规范化与去数字筛查",
             "freeze": "本人集中确认全部数据并保存哈希", "verify-freeze": "验证冻结内容与原始文件",
             "export-train": "从核心题目派生G1至G4", "serve-review": "启动仅本机访问的人工审核界面"}
    for name, desc in names.items():
        q = sub.add_parser(name, help=desc, description=desc)
        q.add_argument("--config", default="configs/project.yaml")
        if name in {"model-review", "revise-knowledge", "plan-cases", "import-data"}:
            q.add_argument("--file", type=Path, required=True)
        if name == "model-review":
            q.add_argument("--entity", choices=["knowledge", "data"], default="knowledge")
        if name in {"review-knowledge", "review-data"}:
            q.add_argument("--id", required=True)
            q.add_argument("--decision", choices=["approved", "returned", "discarded"], required=True)
        if name in {"review-knowledge", "review-data", "confirm-batch", "freeze"}:
            q.add_argument("--reviewer", required=True)
            q.add_argument("--date", required=True, help="真实确认日期 YYYY-MM-DD")
            q.add_argument("--notes", required=True)
        if name in {"sample-batch", "confirm-batch"}:
            q.add_argument("--batch", required=True)
        if name in {"prepare-generation", "import-data", "review-data"}:
            q.add_argument("--split", choices=["train", "diagnostic", "validation", "test", "retention"], required=True)
        if name in {"prepare-generation", "review-view", "export-train"}:
            q.add_argument("--output", type=Path, required=True)
        if name == "serve-review":
            q.add_argument("--port", type=int, default=8765)
    return p


def mutable(freeze):
    if freeze["status"] == "frozen":
        raise ValueError("已冻结，须另建版本，不原地改动")


def review_identity(args):
    from datetime import date
    date.fromisoformat(args.date)
    if not args.reviewer.strip() or args.reviewer.lower() in {"codex", "ai", "model", "mock", "assistant"} or not args.notes.strip():
        raise ValueError("需真实本人身份、日期与说明")
    return {"reviewer": args.reviewer, "date": args.date, "notes": args.notes}


def run(args):
    cfg, project, root = config(args.config)
    cmd = args.command
    if cmd == "serve-review":
        from .review_ui import serve
        serve(args.config, args.port)
        return {"status": "stopped"}
    if cmd == "doctor":
        sources = yaml.safe_load((project / cfg["sources"]).read_text(encoding="utf-8"))["sources"]
        for s in sources:
            if not (project / s["file_path"]).is_file():
                raise ValueError(f"来源路径不存在：{s['file_path']}")
        return {"workspace": str(root), "active": [s["file_path"] for s in sources if s["enabled"]],
                "deferred": [s["file_path"] for s in sources if not s["enabled"]]}
    # 生成任务只读sources、knowledge和蓝图，不加载合并data里的任何测试题。
    if cmd == "prepare-generation":
        sources = read_jsonl(root / "sources.jsonl")
        cards = read_jsonl(root / "knowledge.jsonl")
        freeze = json.loads((root / "freeze.json").read_text(encoding="utf-8"))
        validate_knowledge(sources, cards, project)
        selected = [b for b in freeze["case_blueprints"] if b["split"] == args.split]
        if not selected:
            raise ValueError("没有指定集合的已分配蓝图")
        kids = {k for b in selected for k in b["knowledge_ids"]}
        cards = [c for c in cards if c["knowledge_id"] in kids]
        if len(cards) != len(kids) or not all(approved(c) for c in cards):
            raise ValueError("蓝图知识未全部经本人确认")
        fids = {r["fragment_id"] for c in cards for r in c["evidence_refs"]}
        if args.split == "test" and "sealed_test" not in args.output.parts:
            raise ValueError("测试生成任务包应放在sealed_test目录")
        write_json(args.output, {"split": args.split, "blueprints": selected, "knowledge": cards,
                                "sources": [s for s in sources if s["fragment_id"] in fids]})
        return {"blueprints": len(selected), "output": str(args.output)}
    sources, cards, data, freeze = load(root)
    cmap = unique(cards, "knowledge_id")
    if cmd == "verify-freeze":
        integrity = freeze.get("integrity_sha256")
        if freeze["status"] != "frozen" or integrity != digest({k: v for k, v in freeze.items() if k != "integrity_sha256"}):
            raise ValueError("不是有效冻结版本或freeze内容被修改")
        for name, sha in freeze["hashes"].items():
            if file_hash(project / name) != sha:
                raise ValueError(f"冻结哈希不匹配：{name}")
        return {"status": "verified", "files": len(freeze["hashes"])}
    stats = validate_knowledge(sources, cards, project)
    if cmd == "validate":
        dev = [q for q in data if q["split"] != "test"]
        check_questions(dev, cmap, sources, freeze)
        return {**stats, "development_questions": len(dev), "test_content_validated": False}
    if cmd == "review-view":
        from .review_view import render
        render(cards, sources, args.output)
        return {"output": str(args.output), "knowledge_cards": len(cards)}
    if cmd != "export-train":
        mutable(freeze)
    if cmd == "model-review":
        incoming = json.loads(args.file.read_text(encoding="utf-8-sig"))
        rows = cards if args.entity == "knowledge" else data
        key = "knowledge_id" if args.entity == "knowledge" else "question_id"
        mapping = unique(rows, key)
        seen = set()
        for item in incoming["reviews"]:
            rid = item[key]
            if rid in seen:
                raise ValueError("重复的审核编号")
            seen.add(rid)
            row = mapping[rid]
            if item["reviewed_hash"] != content_hash(row):
                raise ValueError("模型审核的内容哈希已过期")
            row["model_review"] = {"status": item["status"], "session_id": incoming["session_id"],
                                   "issues": item["issues"], "revisions": item.get("revisions", []), "reviewed_hash": item["reviewed_hash"]}
            validate_review(row)
        save_knowledge(root, cards, data, freeze)
        return {"model_reviewed": len(seen), "human_confirmations_added": 0}
    if cmd == "review-knowledge":
        review_identity(args)
        cards = human_decide(cards, "knowledge_id", args.id, args.decision, args.reviewer, args.date, args.notes)
        impacted = [q["question_id"] for q in data if args.id in q["knowledge_ids"]]
        if args.decision == "discarded":
            freeze.setdefault("discarded_questions", []).extend(q for q in data if args.id in q["knowledge_ids"])
            data = [q for q in data if args.id not in q["knowledge_ids"]]
        save_knowledge(root, cards, data, freeze)
        return {"decision": args.decision, "affected_questions": impacted}
    if cmd == "revise-knowledge":
        new = json.loads(args.file.read_text(encoding="utf-8-sig"))
        old = cmap[new["knowledge_id"]]
        if new["revision"] != old["revision"] + 1:
            raise ValueError("修订必须保留ID并将revision加1")
        new["revision_history"] = old["revision_history"] + [{k: v for k, v in old.items() if k != "revision_history"}]
        new["human_review"] = {"status": "pending", "reviewer": None, "date": None, "notes": None, "reviewed_hash": None}
        new["model_review"] = {"status": "pending", "session_id": None, "issues": [], "revisions": [], "reviewed_hash": None}
        cmap[new["knowledge_id"]] = new
        cards = list(cmap.values())
        validate_knowledge(sources, cards, project)
        affected = []
        for q in data:
            if new["knowledge_id"] in q["knowledge_ids"]:
                affected.append(q["question_id"])
                q["model_review"] = {"status": "pending", "session_id": None, "issues": ["来源知识修订，需重新生成或审核"], "revisions": [], "reviewed_hash": None}
                q["human_review"] = {"status": "pending", "reviewer": None, "date": None, "notes": None, "reviewed_hash": None}
        save_knowledge(root, cards, data, freeze)
        return {"revision": new["revision"], "affected_questions": affected}
    if cmd == "plan-cases":
        if data:
            raise ValueError("已有题目时不可原地重分族，请建立新版本")
        freeze["case_blueprints"] = assign_cases(read_jsonl(args.file), cmap, cfg["seed"])
        write_json(root / "freeze.json", freeze, replace=True)
        return {"blueprints": len(freeze["case_blueprints"])}
    if cmd == "import-data":
        incoming = read_jsonl(args.file, sealed=args.split == "test")
        if any(q["split"] != args.split for q in incoming):
            raise ValueError("每次只能导入明确指定集合的生成批次")
        data += incoming
        check_questions(data, cmap, sources, freeze)
        write_jsonl(root / "data.jsonl", data, replace=True)
        return {"imported": len(incoming), "split": args.split}
    if cmd == "review-data":
        review_identity(args)
        if unique(data, "question_id")[args.id]["split"] != args.split:
            raise ValueError("题目不属于所指定的审核集合")
        data = human_decide(data, "question_id", args.id, args.decision, args.reviewer, args.date, args.notes)
        write_jsonl(root / "data.jsonl", data, replace=True)
        return {"decision": args.decision}
    if cmd in {"sample-batch", "confirm-batch"}:
        check_questions([q for q in data if q["split"] == "train"], cmap, sources, freeze)
        if cmd == "sample-batch":
            record = sample_batch(data, cmap, freeze, args.batch, cfg["seed"])
            old = freeze["training_batches"].get(args.batch)
            if old and old["status"] == "approved":
                raise ValueError("已确认批次不能重抽以挑选样本")
            freeze["training_batches"][args.batch] = record
        else:
            identity = review_identity(args)
            record = freeze["training_batches"][args.batch]
            mapping = unique(data, "question_id")
            for qid, sha in record["record_hashes"].items():
                if content_hash(mapping[qid]) != sha:
                    raise ValueError("批次题目已修改，重新全量模型审核并抽查")
                if mapping[qid]["model_review"]["status"] != "passed" or mapping[qid]["human_review"]["status"] in {"returned", "discarded"}:
                    raise ValueError("批次存在未解决错误")
            if any(mapping[qid]["human_review"]["status"] != "approved" for qid in record["sample_ids"]):
                raise ValueError("抽中的题目尚未全部由本人通过")
            record.update(status="approved", human_confirmation=identity)
        write_json(root / "freeze.json", freeze, replace=True)
        return record
    if cmd == "dedup":
        pairs = duplicate_pairs([q for q in data if q["split"] != "test"])
        freeze["development_duplicate_candidates"] = pairs
        write_json(root / "freeze.json", freeze, replace=True)
        return {"candidates": len(pairs), "location": "freeze.json/development_duplicate_candidates"}
    if cmd == "export-train":
        train = [q for q in data if q["split"] == "train"]
        if not train:
            raise ValueError("没有通过检查的真实训练数据，不导出空文件")
        check_questions(train, cmap, sources, freeze)
        groups = export_groups(train, sources, freeze)
        for group, rows in groups.items():
            write_jsonl(args.output / f"{group}.jsonl", rows)
        return {g: len(rows) for g, rows in groups.items()}
    if cmd == "freeze":
        identity = review_identity(args)
        if not data or not all(approved(c) for c in cards):
            raise ValueError("需要真实题目及本人确认的知识卡，不能冻结空数据")
        check_questions(data, cmap, sources, freeze)
        for q in data:
            if q["split"] == "train":
                if not training_ready(q, freeze):
                    raise ValueError("训练批次未通过本人抽查")
            elif q["human_review"]["status"] != "approved":
                raise ValueError("诊断和评价题需要本人逐题/逐组确认")
        pairs = duplicate_pairs(data)
        freeze["duplicate_candidates"] = pairs
        write_json(root / "freeze.json", freeze, replace=True)
        decisions = {d["pair_id"]: d for d in freeze["dedup_reviews"]}
        for pair in pairs:
            decision = decisions.get(pair["pair_id"])
            if "normalized" in pair["methods"] or not decision or decision.get("decision") != "distinct" or decision.get("hashes") != pair["hashes"] or not decision.get("reviewer"):
                raise ValueError("存在重复或未裁决结构疑似对，不能冻结")
        tested = {k for q in data if q["split"] == "test" for k in q["knowledge_ids"]}
        for pool in ("ordinary", "coverage"):
            covered = {k for q in data if q["split"] == "train" and q["pool"] == pool for k in q["knowledge_ids"]}
            if not covered or not tested <= covered:
                raise ValueError("正式测试知识须在两个训练池均有覆盖")
        freeze.update(status="frozen", confirmation=identity, stats={**stats, "questions": len(data)})
        paths = [root / f for f in ("sources.jsonl", "knowledge.jsonl", "data.jsonl")]
        paths += [project / s["file_path"] for s in sources]
        paths += [Path(args.config).resolve(), project / cfg["sources"], *sorted((project / "prompts").glob("*.md")), *sorted((project / "src/procurement_transfer").glob("*.py"))]
        freeze["hashes"] = {str(p.resolve().relative_to(project)): file_hash(p) for p in set(paths)}
        freeze["integrity_sha256"] = digest({k: v for k, v in freeze.items() if k != "integrity_sha256"})
        write_json(root / "freeze.json", freeze, replace=True)
        return {"status": "frozen", "questions": len(data), "targets_are_blocking": False}
    raise ValueError("未知命令")


def main():
    args = parser().parse_args()
    try:
        print(json.dumps(run(args), ensure_ascii=False, indent=2))
    except (ValueError, KeyError, FileNotFoundError) as exc:
        print(f"错误：{exc}", file=sys.stderr)
        raise SystemExit(2)
