"""화면 없이 실행하는 CLI (회귀 테스트·배치 실행용).

  python -m exam_gen.cli new  "과정명" 원고.pdf [--offset 17]
  python -m exam_gen.cli run  <프로젝트> --total 30 [--pages 0,1] [--provider mock]
  python -m exam_gen.cli export <프로젝트> [--draft]
  python -m exam_gen.cli report <프로젝트>
  python -m exam_gen.cli pool <프로젝트> [--provider ...]   # 최종 풀 점검(같은 원리 반복 묶음)
"""
from __future__ import annotations

import argparse
import json
from collections import Counter

from .config import criteria
from .generate.generator import start_batch
from .llm import settings as llm_settings
from .output import evidence_pdf, excel
from .plan import calibrate
from .plan.coverage import allocate, largest_remainder
from .plan.slots import build_slots
from .store import Project, set_last_project


def main(argv=None):
    ap = argparse.ArgumentParser(prog="exam_gen")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("new")
    a.add_argument("name")
    a.add_argument("files", nargs="+")
    a.add_argument("--offset", type=int, help="PDF 1쪽의 책 쪽수를 확정(생략 시 자동 감지값으로 확정)")
    r = sub.add_parser("run")
    r.add_argument("project")
    r.add_argument("--total", type=int, default=30)
    r.add_argument("--pages", help="출제할 페이지 인덱스(0부터, 쉼표) — 지정 시 해당 페이지에만 균등 배분")
    r.add_argument("--provider", help="mock | claude_agent_sdk | codex_cli | anthropic | openai_compat")
    e = sub.add_parser("export")
    e.add_argument("project")
    e.add_argument("--draft", action="store_true")
    rp = sub.add_parser("report")
    rp.add_argument("project")
    pl = sub.add_parser("pool")
    pl.add_argument("project")
    pl.add_argument("--provider")
    args = ap.parse_args(argv)

    if args.cmd == "new":
        p = Project.create(args.name)
        for f in args.files:
            s = p.add_source(f)
            off = args.offset if args.offset is not None else s["page_map"]["offset"]
            p.set_page_map(s["id"], off, s["page_map"]["mode"], True)
            print(f"{f}: {s['pages']}쪽, PDF 1쪽 = 책 {off}쪽")
        print("프로젝트:", p.slug)
        set_last_project(p.slug, by="cli")
    elif args.cmd == "run":
        p = Project(args.project)
        set_last_project(p.slug, by="cli")  # 대시보드가 이 프로젝트를 열어 진행 상황을 보여 준다
        s = llm_settings.load()
        if args.provider:  # 생성·G4·G5 모두 이 공급자로
            s.provider, s.provider_solve, s.provider_review = args.provider, "", ""
        opts = {**criteria()["defaults"], **p.meta.get("options", {})}
        pages = p.pages
        if args.pages:
            sel = [int(x) for x in args.pages.split(",")]
            alloc = allocate([pg for pg in pages if pg["g"] in sel], args.total)
        else:
            alloc = allocate(pages, args.total)
        start = sum(len(b["slots"]) for b in p.batches()) + 1
        diff_counts = None
        if opts.get("difficulty_calibrate", True):  # 난이도 보정 출제(plan/calibrate.py) — 대시보드와 같게
            n = sum(alloc.values())
            diff_counts = calibrate.plan(n, opts["difficulty_pct"], largest_remainder(n, opts["type_pct"]),
                                         calibrate.table()[0])
        slots = build_slots(pages, alloc, opts, difficulty_counts=diff_counts, start_index=start, seed=start)
        b = start_batch(p, s, slots, opts, kind="initial", background=False)
        print(json.dumps(p.read_progress(b), ensure_ascii=False, indent=1))
    elif args.cmd == "export":
        p = Project(args.project)
        items = p.items()
        print(excel.write(p, items, p.dir / "exports" / "평가문항.xlsx", draft=args.draft)["path"])
        for mode in ("extract", "overview"):
            r = evidence_pdf.build(p, items, p.dir / "exports" / f"근거PDF_{mode}.pdf", mode=mode, draft=args.draft)
            print(r["path"], r["items"], "문항", r["pages"], "쪽", "하이라이트 없음:", r["no_highlight"])
    elif args.cmd == "pool":
        from .validate import pool_review
        p = Project(args.project)
        s = llm_settings.load()
        if args.provider:
            s.provider, s.provider_solve, s.provider_review = args.provider, "", ""
        r = pool_review.run(p, s)
        print(f"점검 {r.get('items', 0)}문항 · 같은 원리 묶음 {len(r['groups'])}개 · 표시 {r['flagged']}문항")
        for g in r["groups"]:
            print(f"  - {g['principle']}: {', '.join(g['keys'])}  ({g['note']})")
    elif args.cmd == "report":
        p = Project(args.project)
        items = p.items()
        print(Counter(it["status"] for it in items))
        for it in items:
            print(f"\n[{it['key']}] {it['status']} · {it['type']} · {it['difficulty']} · p.{[p.pages[g]['label'] for g in it.get('source_pages') or []]}")
            print("  발문:", it["stem"])
            for st in it.get("statements") or []:
                print(f"   {st['label']}. {st['text']} ({'참' if st['is_true'] else '거짓'})")
            for i, c in enumerate(it.get("choices") or [], 1):
                print(f"   {i}) {c['text']} ({'참' if c.get('is_true') else '거짓'})")
            print("  정답:", it["answer"], "| 해설:", it["explanation"])
            for ev in it.get("evidence") or []:
                m = ev.get("match")
                print(f"   근거[{ev['target']}] {'O' if m else 'X'} {m['level'] if m else ''} \"{ev['quote'][:70]}\"")
            for f in it["validation"].get("fails", []) + it["validation"].get("warnings", []):
                print("  !", f[:160])


if __name__ == "__main__":
    main()
