"""Local, dependency-free human review desk backed by the four core files."""
import html
import json
import secrets
from collections import Counter
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import RLock
from urllib.parse import parse_qs, quote, urlsplit

from .cli import config, review_identity, run
from .core import content_hash, load, unique, validate_knowledge
from .dataset import assign_cases, duplicate_pairs
from .io import digest, write_json


def esc(value):
    if value is None:
        value = "未说明"
    elif not isinstance(value, str):
        try:
            value = json.dumps(value, ensure_ascii=False, indent=2)
        except TypeError:
            value = str(value)
    return html.escape(value, quote=True)


def _args(config_path, command, **kwargs):
    from argparse import Namespace
    return Namespace(config=config_path, command=command, **kwargs)


def _identity(form):
    info = _args("", "", reviewer=form.get("reviewer", "").strip(),
                 date=form.get("date", ""), notes=form.get("notes", "").strip())
    return review_identity(info)


def _field(form, key):
    value = form.get(key, "").strip()
    if not value:
        raise ValueError(f"缺少 {key}")
    return value


def _review_snapshot(row):
    return digest({"content": content_hash(row), "human_review": row["human_review"]})


def perform(config_path, form):
    """Perform one reviewed action; all writes use the existing data contracts."""
    _, project, root = config(config_path)
    sources, cards, data, freeze = load(root)
    validate_knowledge(sources, cards, project)
    op = _field(form, "op")
    if freeze["status"] == "frozen":
        raise ValueError("该版本已经冻结；请先建立新版本")
    if op in {"review_knowledge", "review_data"}:
        kind = op.split("_")[1]
        key = "knowledge_id" if kind == "knowledge" else "question_id"
        row_id = _field(form, "id")
        row = unique(cards if kind == "knowledge" else data, key)[row_id]
        if _review_snapshot(row) != _field(form, "expected_hash"):
            raise ValueError("页面已过期；请刷新后重新核对")
        identity = _identity(form)
        decision = _field(form, "decision")
        if decision not in {"approved", "returned", "discarded"}:
            raise ValueError("无效审核决定")
        kwargs = dict(id=row_id, decision=decision, **identity)
        if kind == "data":
            kwargs["split"] = row["split"]
        run(_args(config_path, "review-" + kind, **kwargs))
        if kind == "knowledge":
            return f"/knowledge?id={quote(row_id)}"
        return f"/questions?split={quote(row['split'])}&id={quote(row_id)}"
    if op == "plan_cases":
        _identity(form)
        if data or freeze["case_blueprints"]:
            raise ValueError("已有蓝图或题目；不可从界面原地重分族")
        raw = _field(form, "blueprints")
        if len(raw) > 400000:
            raise ValueError("蓝图输入过大")
        rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
        planned = assign_cases(rows, unique(cards, "knowledge_id"),
                               config(config_path)[0]["seed"])
        if not {"diagnostic", "train", "validation", "test"} <= {b["split"] for b in planned}:
            raise ValueError("蓝图需覆盖诊断、训练、验证和测试四种集合")
        freeze["case_blueprints"] = planned
        freeze["blueprint_review"] = {**_identity(form), "reviewed_hash": digest(planned)}
        write_json(root / "freeze.json", freeze, replace=True)
        return "/blueprints"
    if op == "confirm_blueprints":
        planned = freeze["case_blueprints"]
        if not planned or digest(planned) != _field(form, "expected_hash"):
            raise ValueError("蓝图已变化；请刷新并重新核对")
        freeze["blueprint_review"] = {**_identity(form), "reviewed_hash": digest(planned)}
        write_json(root / "freeze.json", freeze, replace=True)
        return "/blueprints"
    if op == "sample_batch":
        batch = _field(form, "batch")
        run(_args(config_path, "sample-batch", batch=batch))
        return f"/batches?batch={quote(batch)}"
    if op == "confirm_batch":
        batch = _field(form, "batch")
        record = freeze["training_batches"][batch]
        if digest(record) != _field(form, "expected_hash"):
            raise ValueError("批次抽查记录已改变；请刷新")
        run(_args(config_path, "confirm-batch", batch=batch, **_identity(form)))
        return f"/batches?batch={quote(batch)}"
    if op == "scan_duplicates":
        # The final scan is explicit; ordinary development scans do not expose test questions.
        scope = _field(form, "scope")
        if scope == "development":
            run(_args(config_path, "dedup"))
        elif scope == "final":
            freeze["duplicate_candidates"] = duplicate_pairs(data)
            write_json(root / "freeze.json", freeze, replace=True)
        else:
            raise ValueError("无效查重范围")
        return "/duplicates"
    if op == "review_duplicate":
        pair_id = _field(form, "pair_id")
        pairs = {p["pair_id"]: p for p in duplicate_pairs(data)}
        pair = pairs[pair_id]
        if digest(pair) != _field(form, "expected_hash"):
            raise ValueError("题目内容已改变；请重新查重")
        decision = _field(form, "decision")
        if decision not in {"distinct", "duplicate"}:
            raise ValueError("无效去重裁决")
        if decision == "distinct" and "normalized" in pair["methods"]:
            raise ValueError("规范化完全相同的题必须修订或剔除")
        identity = _identity(form)
        freeze["dedup_reviews"] = [r for r in freeze["dedup_reviews"] if r["pair_id"] != pair_id]
        freeze["dedup_reviews"].append({"pair_id": pair_id, "decision": decision,
                                         "hashes": pair["hashes"], **identity})
        write_json(root / "freeze.json", freeze, replace=True)
        return "/duplicates"
    if op == "freeze":
        identity = _identity(form)
        if not form.get("final_check"):
            raise ValueError("请先确认全部审核和评分口径")
        run(_args(config_path, "freeze", **identity))
        return "/freeze"
    raise ValueError("未知操作")


STYLE = """
body{font:15px/1.65 system-ui,'Microsoft YaHei',sans-serif;color:#183047;background:#f2f5f7;margin:0}
header{background:#173a4d;color:white;padding:16px max(18px,calc((100vw - 1240px)/2));position:sticky;top:0;z-index:2}
nav a{color:#dcecf3;margin-right:18px;text-decoration:none}nav a:hover{text-decoration:underline}
main{max-width:1240px;margin:24px auto;padding:0 18px}h1,h2,h3{line-height:1.3}
.card{background:white;border:1px solid #d9e3e8;border-radius:12px;padding:18px;margin:16px 0;box-shadow:0 2px 8px #1830470b}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.three{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
pre{white-space:pre-wrap;overflow-wrap:anywhere;font:14px/1.55 system-ui;margin:7px 0 14px}
small,.muted{color:#607487}.pill{border-radius:99px;padding:2px 9px;background:#e7f0f5;font-size:13px}
.approved,.passed{background:#def2e5}.returned,.needs_revision,.discarded{background:#fde5dc}.pending{background:#fff1ce}
label{display:block;margin:9px 0 4px;font-weight:600}input,textarea,select{font:inherit;padding:8px;border:1px solid #afc0ca;border-radius:6px;box-sizing:border-box;max-width:100%}
input[type=text],textarea{width:100%}textarea{min-height:68px}button{background:#176d78;color:white;border:0;border-radius:6px;padding:9px 16px;cursor:pointer;margin-top:10px}button:hover{background:#11545d}
a{color:#12616c}.list{display:flex;flex-wrap:wrap;gap:8px}.list a{padding:4px 9px;background:white;border:1px solid #d5e1e6;border-radius:6px;text-decoration:none}
.notice{padding:12px 16px;background:#fff6d8;border-left:4px solid #e4ae3b}.error{background:#ffe4df;border-color:#d95238}
table{border-collapse:collapse;width:100%;background:white}td,th{border-bottom:1px solid #e1e9ed;padding:8px;text-align:left;vertical-align:top}
@media(max-width:800px){.grid,.three{grid-template-columns:1fr}header{position:static}}
"""


def _layout(title, body):
    nav = '<nav><a href="/">总览</a><a href="/knowledge">知识卡</a><a href="/blueprints">案例蓝图</a><a href="/questions">题目</a><a href="/batches">批次</a><a href="/duplicates">去重</a><a href="/freeze">冻结</a></nav>'
    return f'<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(title)} · 采购知识审核</title><style>{STYLE}</style><header><strong>采购知识应用迁移 · 本人审核台</strong>{nav}</header><main><h1>{esc(title)}</h1>{body}</main></html>'


def _hidden(token, **values):
    fields = {"csrf": token, **values}
    return "".join(f'<input type="hidden" name="{esc(k)}" value="{esc(v)}">' for k, v in fields.items())


def _identity_form():
    return f'<div class="grid"><div><label>审核人真实姓名</label><input name="reviewer" type="text" required autocomplete="name"></div><div><label>审核日期</label><input name="date" type="date" required value="{date.today().isoformat()}"></div></div><label>依据／处理说明</label><textarea name="notes" required placeholder="说明核对了什么、如何处理疑点"></textarea>'


def _review_form(token, op, row, key):
    return (f'<form method="post" action="/action">{_hidden(token, op=op, id=row[key], expected_hash=_review_snapshot(row))}'
            '<label>审核决定</label><select name="decision"><option value="approved">通过</option><option value="returned">退回修订</option><option value="discarded">弃用</option></select>'
            + _identity_form() + '<button type="submit">保存本人审核</button></form>')


def render_page(config_path, path, query, token):
    _, project, root = config(config_path)
    sources, cards, data, freeze = load(root)
    fragments = unique(sources, "fragment_id")
    cards_by_id = unique(cards, "knowledge_id")
    q_by_id = unique(data, "question_id") if data else {}
    frozen = freeze["status"] == "frozen"
    disabled = '<p class="notice">此版本已冻结，界面只读。</p>' if frozen else ""
    if path == "/":
        counts = Counter(q["split"] for q in data)
        pending_cards = sum(c["human_review"]["status"] != "approved" for c in cards)
        pending_questions = sum(q["human_review"]["status"] == "pending" for q in data if q["split"] != "train")
        body = (f'<div class="three"><div class="card"><h2>知识卡</h2><p>{len(cards)} 张；待本人确认 {pending_cards} 张</p><a href="/knowledge">逐卡审核 →</a></div>'
                f'<div class="card"><h2>题目</h2><p>诊断 {counts["diagnostic"]} · 训练 {counts["train"]} · 验证 {counts["validation"]} · 测试 {counts["test"]}</p><p>非训练待确认 {pending_questions} 题</p><a href="/questions">查看题目 →</a></div>'
                f'<div class="card"><h2>数据状态</h2><p>{esc(freeze["status"])}；蓝图 {len(freeze["case_blueprints"])} 条；已确认训练批次 {sum(x["status"] == "approved" for x in freeze["training_batches"].values())} 个</p><a href="/freeze">冻结检查 →</a></div></div>'
                '<p class="notice">模型复核通过不等于本人确认。页面只在 127.0.0.1 开放；题目仍需先按现有命令导入。测试题仅在指定页面查看，不会加入训练生成任务。</p>' + disabled)
        return _layout("审核总览", body)
    if path == "/knowledge":
        selected = query.get("id", [None])[0]
        filters = '<div class="list">' + ''.join(f'<a href="/knowledge?id={quote(c["knowledge_id"])}">{i:02d} {esc(c["name"])} <span class="pill {esc(c["human_review"]["status"])}">{esc(c["human_review"]["status"])}</span></a>' for i, c in enumerate(cards, 1)) + '</div>'
        if selected is None:
            return _layout("知识卡审核", '<p>选择一张卡，对照原文后逐张确认。</p>' + filters)
        c = cards_by_id[selected]
        left = ''.join(f'<h3>{label}</h3><pre>{esc(c[field])}</pre>' for label, field in (("知识内容", "content"), ("范围", "scope"), ("条件", "conditions"), ("例外", "exceptions"), ("公式", "formula_spec"), ("允许操作", "supported_operations"), ("模型复核", "model_review"), ("本人审核", "human_review")))
        right = ""
        for ref in c["evidence_refs"]:
            s = fragments[ref["fragment_id"]]
            right += f'<div class="card"><strong>{esc(s["title"])} · {esc(s["locator"])}</strong><h3>引用原句</h3><pre>{esc(ref["quote"])}</pre><p>支持字段：{esc(ref["fields"])}</p><p>抽取疑点：{esc(s["issues"])}</p><details><summary>完整原文片段</summary><pre>{esc(s["raw_text"])}</pre></details></div>'
        body = f'<p><a href="/knowledge">← 全部知识卡</a> · {esc(c["knowledge_id"])} · revision {c["revision"]}</p><div class="grid"><section class="card">{left}</section><section>{right}</section></div>'
        if not frozen:
            body += '<section class="card"><h2>本人决定</h2>' + _review_form(token, "review_knowledge", c, "knowledge_id") + '</section>'
        return _layout(c["name"], body)
    if path == "/blueprints":
        rows = freeze["case_blueprints"]
        body = f'<p>已规划 {len(rows)} 条；同构案例必须属于同一族。审核知识范围、操作支持和集合分配后再生成题目。</p>'
        if rows:
            body += '<table><tr><th>蓝图／族</th><th>集合</th><th>操作／知识</th><th>结构</th></tr>' + ''.join(f'<tr><td>{esc(b["blueprint_id"])}<br>{esc(b["case_family_id"])}</td><td>{esc(b["split"])}</td><td>{esc(b["operation"])}<br>{esc(b["knowledge_ids"])}</td><td>{esc(b["structure"])}</td></tr>' for b in rows) + '</table>'
            reviewed = freeze.get("blueprint_review")
            current = reviewed and reviewed.get("reviewed_hash") == digest(rows)
            body += f'<p>本人规划确认：{esc(reviewed if current else "待确认")}</p>'
            if not frozen and not current:
                body += f'<form class="card" method="post" action="/action">{_hidden(token, op="confirm_blueprints", expected_hash=digest(rows))}' + _identity_form() + '<button>确认本版案例族与集合</button></form>'
        elif not frozen:
            body += ('<p class="notice">粘贴完整 JSONL；在任何题目导入前一次性规划诊断、训练、验证和测试蓝图。此操作会直接提交，提交前请逐族核对。</p>'
                     f'<form class="card" method="post" action="/action">{_hidden(token, op="plan_cases")}<label>案例蓝图 JSONL</label><textarea name="blueprints" rows="12" required></textarea>'
                     + _identity_form() + '<button type="submit">确认并分配案例族</button></form>')
        return _layout("案例蓝图审核", body + disabled)
    if path == "/questions":
        selected = query.get("id", [None])[0]
        split = query.get("split", ["diagnostic"])[0]
        if split not in {"diagnostic", "train", "validation", "test", "retention"}:
            raise ValueError("无效集合")
        tabs = '<div class="list">' + ''.join(f'<a href="/questions?split={s}">{s} ({sum(q["split"] == s for q in data)})</a>' for s in ("diagnostic", "train", "validation", "test", "retention")) + '</div>'
        if selected is None:
            rows = [q for q in data if q["split"] == split]
            listing = '<div class="list">' + ''.join(f'<a href="/questions?split={quote(split)}&id={quote(q["question_id"])}">{esc(q["question_id"])} <span class="pill {esc(q["human_review"]["status"])}">{esc(q["human_review"]["status"])}</span></a>' for q in rows) + '</div>'
            return _layout("题目审核", tabs + f'<p>{esc(split)} 共 {len(rows)} 题。验证／测试须逐组核对 original、irrelevant、critical 三题。</p>' + listing)
        q = q_by_id[selected]
        if q["split"] == "test" and split != "test":
            raise ValueError("测试题只能从测试页打开")
        related = [x for x in data if q["group_id"] and x["group_id"] == q["group_id"]]
        group_links = '<div class="list">' + ''.join(f'<a href="/questions?split={quote(x["split"])}&id={quote(x["question_id"])}">{esc(x["variant_type"])} · {esc(x["question_id"])}</a>' for x in related) + '</div>'
        info = (("业务情境", "context"), ("问题", "question"), ("参考结论", "canonical_reference"), ("普通目标", "plain_target"), ("要素目标", "semantic_target"), ("评分规则", "scoring_spec"), ("模型复核", "model_review"), ("本人审核", "human_review"))
        left = ''.join(f'<h3>{label}</h3><pre>{esc(q[field])}</pre>' for label, field in info)
        if q["split"] == "diagnostic":
            for field in ("diagnostic_predictions", "closed_book_prediction", "open_book_prediction", "diagnostic_judgement"):
                if field in q:
                    left += f'<h3>{esc(field)}</h3><pre>{esc(q[field])}</pre>'
        right = f'<p>集合：{esc(q["split"])} · 池：{esc(q["pool"])} · 批次：{esc(q["batch_id"])}<br>族：{esc(q["case_family_id"])} · 组：{esc(q["group_id"])} · 变体：{esc(q["variant_type"])}<br>知识：{esc(q["knowledge_ids"])}</p>{group_links}'
        for ref in q["evidence_refs"]:
            s = fragments[ref["fragment_id"]]
            right += f'<div class="card"><strong>{esc(s["title"])} · {esc(s["locator"])}</strong><pre>{esc(ref["quote"])}</pre><details><summary>完整原文片段</summary><pre>{esc(s["raw_text"])}</pre></details></div>'
        body = tabs + f'<div class="grid"><section class="card">{left}</section><section class="card">{right}</section></div>'
        if not frozen:
            body += '<section class="card"><h2>本人决定</h2>' + _review_form(token, "review_data", q, "question_id") + '</section>'
        return _layout(q["question_id"], body)
    if path == "/batches":
        batches = freeze["training_batches"]
        ids = sorted({q["batch_id"] for q in data if q["split"] == "train"})
        selected = query.get("batch", [None])[0]
        body = '<p>模型逐题通过后抽样；首批每池 20 题及高风险／疑难题须本人逐题确认。实质错误应停批修订。</p><div class="list">' + ''.join(f'<a href="/batches?batch={quote(b)}">{esc(b)} · {esc(batches.get(b, {}).get("status", "未抽样"))}</a>' for b in ids) + '</div>'
        if selected:
            if selected not in ids:
                raise KeyError(selected)
            record = batches.get(selected)
            if not record and not frozen:
                body += f'<form class="card" method="post" action="/action">{_hidden(token, op="sample_batch", batch=selected)}<button>运行批次抽样</button></form>'
            elif record:
                mapping = unique(data, "question_id")
                links = '<div class="list">' + ''.join(f'<a href="/questions?split=train&id={quote(qid)}">{esc(qid)} · {esc(mapping[qid]["human_review"]["status"])}</a>' for qid in record["sample_ids"] if qid in mapping) + '</div>'
                body += f'<section class="card"><h2>抽中 {len(record["sample_ids"])} 题</h2>{links}<p>池数量：{esc(record["pool_counts"])}</p>'
                if record["status"] != "approved" and not frozen:
                    body += f'<form method="post" action="/action">{_hidden(token, op="confirm_batch", batch=selected, expected_hash=digest(record))}' + _identity_form() + '<button>确认抽查通过</button></form>'
                body += '</section>'
        return _layout("训练批次审核", body + disabled)
    if path == "/duplicates":
        scanned = freeze.get("duplicate_candidates", freeze.get("development_duplicate_candidates", []))
        decisions = {r["pair_id"]: r for r in freeze["dedup_reviews"]}
        body = '<p>先看开发集疑似对；全部题目人工审核后再做全量查重。共享原文不算重复，规范化相同题必须修订。</p>'
        if not frozen:
            body += f'<div class="grid"><form class="card" method="post" action="/action">{_hidden(token, op="scan_duplicates", scope="development")}<button>筛查开发题</button></form><form class="card" method="post" action="/action">{_hidden(token, op="scan_duplicates", scope="final")}<button>最终全量查重（含测试题）</button></form></div>'
        body += f'<p>当前疑似对 {len(scanned)} 组。</p>'
        page = int(query.get("page", ["1"])[0])
        if page < 1:
            raise ValueError("页码无效")
        count = 50
        total_pages = max(1, (len(scanned) + count - 1) // count)
        if page > total_pages:
            raise ValueError("页码超出范围")
        if total_pages > 1:
            body += f'<p>第 {page}/{total_pages} 页 · ' + ' '.join(f'<a href="/duplicates?page={i}">{i}</a>' for i in range(1, total_pages + 1)) + '</p>'
        current_pairs = {p["pair_id"]: p for p in duplicate_pairs(data)}
        for pair in scanned[(page - 1) * count:page * count]:
            latest = current_pairs.get(pair["pair_id"])
            if not latest:
                continue
            stale = latest["hashes"] != pair["hashes"]
            q1, q2 = (q_by_id[i] for i in pair["question_ids"])
            side = ''.join(f'<section class="card"><h3>{esc(q["question_id"])} · {esc(q["split"])}</h3><pre>{esc(q["context"])}\n{esc(q["question"])}</pre></section>' for q in (q1, q2))
            decision = decisions.get(pair["pair_id"])
            body += f'<section class="card"><h2>{esc(pair["pair_id"][:12])} · {esc(pair["methods"])} · 跨集 {esc(pair["cross_split"])}</h2><div class="grid">{side}</div><p>裁决：{esc(decision if decision and decision.get("hashes") == latest["hashes"] else "待处理")}</p>'
            if stale:
                body += '<p class="notice">题目已改变，请重新查重。</p>'
            elif not frozen:
                body += f'<form method="post" action="/action">{_hidden(token, op="review_duplicate", pair_id=pair["pair_id"], expected_hash=digest(latest))}'
                body += '<label>裁决</label><select name="decision"><option value="duplicate">重复：需修订／剔除</option>'
                if "normalized" not in pair["methods"]:
                    body += '<option value="distinct">结构相似但确实不同</option>'
                body += '</select>' + _identity_form() + '<button>保存裁决</button></form>'
            body += '</section>'
        return _layout("疑似重复裁决", body + disabled)
    if path == "/freeze":
        counts = Counter(q["split"] for q in data)
        pending = [c["knowledge_id"] for c in cards if c["human_review"]["status"] != "approved"]
        batches = [k for k, v in freeze["training_batches"].items() if v["status"] != "approved"]
        eval_pending = [q["question_id"] for q in data if q["split"] != "train" and q["human_review"]["status"] != "approved"]
        body = f'<div class="card"><p>状态：<strong>{esc(freeze["status"])}</strong>；知识未通过 {len(pending)} 张；评价／诊断未通过 {len(eval_pending)} 题；待确认批次 {len(batches)} 个。</p><p>题数：{esc(dict(counts))}。规模目标不作硬门禁，但证据、模型复核、人工确认、分组隔离、去重裁决和双池测试知识覆盖必须通过。</p></div>'
        if frozen:
            body += f'<p>确认记录：{esc(freeze.get("confirmation"))}</p><p>完整性：{esc(freeze.get("integrity_sha256"))}</p>'
        else:
            body += (f'<form class="card" method="post" action="/action">{_hidden(token, op="freeze")}'
                     '<label><input type="checkbox" name="final_check" value="yes" required> 我已确认知识、三联组、评分口径、去重与训练抽查；当前版本可以冻结</label>'
                     + _identity_form() + '<button>执行冻结校验与确认</button></form>')
        body += '<p class="muted">训练后真实预测的分歧裁决属于后续实验系统；本项目目前没有预测记录格式和评分入口。</p>'
        return _layout("冻结确认", body)
    raise FileNotFoundError(path)


def serve(config_path="configs/project.yaml", port=8765):
    if not 1 <= port <= 65535:
        raise ValueError("端口需在 1—65535 之间")
    config_path = str(Path(config_path).resolve())
    token = secrets.token_urlsafe(32)
    mutex = RLock()

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status, body, headers=None):
            content = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("X-Frame-Options", "DENY")
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(content)

        def _local_request(self):
            return self.headers.get("Host", "").split(":")[0] in {"127.0.0.1", "localhost"}

        def do_GET(self):
            if not self._local_request():
                self._respond(403, "Forbidden")
                return
            parsed = urlsplit(self.path)
            if parsed.path == "/favicon.ico":
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            try:
                with mutex:
                    body = render_page(config_path, parsed.path, parse_qs(parsed.query), token)
                self._respond(200, body)
            except FileNotFoundError as exc:
                self._respond(404, _layout("页面不存在", f'<p class="notice error">{esc(exc)}</p><p><a href="/">返回总览</a></p>'))
            except (ValueError, KeyError, TypeError) as exc:
                self._respond(400, _layout("无法显示", f'<p class="notice error">{esc(exc)}</p><p><a href="/">返回总览</a></p>'))

        def do_POST(self):
            if not self._local_request() or self.headers.get("Origin") not in {None, f"http://127.0.0.1:{port}", f"http://localhost:{port}"}:
                self._respond(403, "Forbidden")
                return
            if urlsplit(self.path).path != "/action":
                self._respond(404, "Not found")
                return
            size = int(self.headers.get("Content-Length", "0"))
            if size <= 0 or size > 500000:
                self._respond(413, "Invalid request size")
                return
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(size).decode("utf-8"), keep_blank_values=True).items()}
            if form.get("csrf") != token:
                self._respond(403, "Invalid request token")
                return
            try:
                with mutex:
                    location = perform(config_path, form)
                self.send_response(303)
                self.send_header("Location", location)
                self.send_header("Content-Length", "0")
                self.end_headers()
            except (ValueError, KeyError, FileNotFoundError, TypeError, json.JSONDecodeError) as exc:
                self._respond(400, _layout("操作未保存", f'<p class="notice error">{esc(exc)}</p><p><a href="/">返回总览</a></p>'))

    with ThreadingHTTPServer(("127.0.0.1", port), Handler) as server:
        print(f"本人审核界面：http://127.0.0.1:{port} （Ctrl+C 停止）", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
