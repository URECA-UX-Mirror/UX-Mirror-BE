"""등록된 Site Instance가 Optimization 후보 풀 전체를 덮고, 변형 사이트가 C0과 라벨 하나만 다른지 (기획안 v1.3 4.4·13.2)."""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "scripts"))

from build_shop import PAGES, token_diff  # noqa: E402

SITE_DIRS = {"C0": "moro-before", "C1": "moro-c1", "C2": "moro-after", "C3": "moro-c3", "C4": "moro-c4"}


def _load(name):
    with open(os.path.join(ROOT, "config", name), encoding="utf-8") as f:
        return json.load(f)


def test_every_candidate_has_one_site_instance():
    pool = {(c["candidateId"], c["label"]) for c in _load("optimization.json")["candidates"]}
    insts = _load("site_instances.json")["instances"]
    registered = [(i["candidateId"], i["label"]) for i in insts]
    assert sorted(registered) == sorted(pool)
    ports = [re.search(r":(\d+)$", i["baseUrl"]).group(1) for i in insts]
    assert len(set(ports)) == len(ports)
    assert len({i["siteInstanceId"] for i in insts}) == len(insts)


def test_variant_sites_differ_from_c0_by_label_only():
    labels = {c["candidateId"]: c["label"] for c in _load("optimization.json")["candidates"]}
    base_dir = os.path.join(ROOT, "shop", SITE_DIRS["C0"], "pages")
    for cid, d in SITE_DIRS.items():
        if cid == "C0":
            continue
        for key in PAGES:
            with open(os.path.join(base_dir, f"{key}.html"), encoding="utf-8") as f:
                a = f.read()
            with open(os.path.join(ROOT, "shop", d, "pages", f"{key}.html"), encoding="utf-8") as f:
                b = f.read()
            diff = [(x.strip(), y.strip()) for x, y in token_diff(a, b)]
            assert diff == [(labels["C0"], labels[cid])], (cid, key, diff[:3])
