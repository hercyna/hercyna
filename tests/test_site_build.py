import json
import shutil
from pathlib import Path

import pytest

from memory_bench.cli import main
from memory_bench.site import build as site_build
from memory_bench.site import mdlite

REPO = Path(__file__).resolve().parents[1]
T = "2026-11-19T12:00:00+00:00"


def _mock_run(path: Path, grade: str, adapters: str) -> None:
    rc = main(["run", "--mock", "--n", "6", "--adapters", adapters, "--budgets", "1000",
               "--repeats", "3", "--boot-reps", "50", "--out", str(path)])
    assert rc == 0
    m = path / "manifest.json"
    man = json.loads(m.read_text())
    man["grade"] = grade
    m.write_text(json.dumps(man))


@pytest.fixture
def root(tmp_path):
    _mock_run(tmp_path / "results" / "runs" / "t1", "table", "bm25")
    _mock_run(tmp_path / "results" / "runs" / "x1", "exploratory", "recent")
    (tmp_path / "protocol").mkdir()
    shutil.copy(REPO / "protocol" / "v1.json", tmp_path / "protocol" / "v1.json")
    return tmp_path


def _build(root, out, **kw):
    return site_build.build(root, out, built_at=T, **kw)


def test_table_run_listed_exploratory_only_with_flag(root, tmp_path):
    _build(root, tmp_path / "a")
    page = (tmp_path / "a" / "index.html").read_text()
    body = page.split("<main")[1]
    assert "maintainer run" in body and 'class="lab x"' not in body and ">recent<" not in body
    _build(root, tmp_path / "b", include_exploratory=True)
    page = (tmp_path / "b" / "index.html").read_text()
    assert "maintainer run" in page and 'class="lab x"' in page and ">recent<" in page


def test_floor_and_columns(root, tmp_path):
    _build(root, tmp_path / "o")
    page = (tmp_path / "o" / "index.html").read_text()
    for label in ("Excess wrong action", "Withheld above floor", "Right action", "View tokens", "per case",
                  "Cost per case", "Cases", "Floor · ", "bm25", 'id="why"', "Run audit", "Frontier",
                  "All v1 cases are synthetic."):
        assert label in page, label


def test_no_rank_when_rule_l_gives_none(root, tmp_path):
    _build(root, tmp_path / "o", include_exploratory=True)
    page = (tmp_path / "o" / "index.html").read_text()
    assert 'class="verdict stop"' in page and "No rank shown" in page and "Order: fixed" in page
    assert page.index(">bm25<") < page.index(">recent<")
    assert 'class="rank"' not in page


def test_missing_copy_shows_pending_block(root, tmp_path):
    _build(root, tmp_path / "o")
    for name in ("method", "submit"):
        page = (tmp_path / "o" / f"{name}.html").read_text()
        assert "copy pending" in page and f"site/copy/{name}.md" in page
    assert "Frozen values" in (tmp_path / "o" / "method.html").read_text()
    assert "bench verify DIR" in (tmp_path / "o" / "submit.html").read_text()


def test_copy_file_rendered(root, tmp_path):
    (root / "site" / "copy").mkdir(parents=True)
    (root / "site" / "copy" / "method.md").write_text("# Head\n\nA `code` <b> para.\n\n- one\n- two\n")
    (root / "site" / "copy" / "limits.md").write_text("## Limits\n\nThe cases are synthetic.\n")
    _build(root, tmp_path / "o")
    page = (tmp_path / "o" / "method.html").read_text()
    assert "<h1>Head</h1>" in page and "<code>code</code>" in page and "&lt;b&gt;" in page
    assert "<li>two</li>" in page
    assert "copy pending" not in page.split("<main")[1].split("</main>")[0]


def test_byte_identical_with_fixed_time(root, tmp_path):
    _build(root, tmp_path / "a", include_exploratory=True)
    _build(root, tmp_path / "b", include_exploratory=True)
    for n in ("index.html", "method.html", "submit.html"):
        assert (tmp_path / "a" / n).read_bytes() == (tmp_path / "b" / n).read_bytes()


@pytest.fixture
def sub_root(tmp_path):
    """A root with one table run (bm25) and an exploratory mock run (recent) packed as a submission."""
    root = tmp_path / "root"
    _mock_run(root / "results" / "runs" / "t1", "table", "bm25")
    (root / "protocol").mkdir()
    shutil.copy(REPO / "protocol" / "v1.json", root / "protocol" / "v1.json")
    run = tmp_path / "side-run"
    rc = main(["run", "--mock", "--n", "6", "--adapters", "recent", "--budgets", "1000", "--repeats", "3",
               "--boot-reps", "50", "--out", str(run)])
    assert rc == 0
    assert main(["submit", "pack", str(run), "--out", str(root / "submissions"), "--exploratory"]) == 0
    sub = next((root / "submissions").rglob("submission.json"))
    data = json.loads(sub.read_text())
    data["submitter"] = "Ann"
    sub.write_text(json.dumps(data))
    return root


def test_verified_submission_shown_as_exploratory(sub_root, tmp_path):
    # v1: a verified outside submission runs on its own keys, so it is exploratory; it shows publicly, labelled
    _build(sub_root, tmp_path / "a")
    body = (tmp_path / "a" / "index.html").read_text().split("<main")[1]
    assert ">recent<" in body and 'class="lab x"' in body


def test_tampered_submission_hidden(sub_root, tmp_path):
    m = next((sub_root / "submissions").rglob("metrics.jsonl"))
    lines = m.read_text().splitlines()
    row = json.loads(lines[0])
    row["action"] = "EXPLAIN" if row["action"] != "EXPLAIN" else "ABSTAIN"
    lines[0] = json.dumps(row, sort_keys=True, separators=(",", ":"))
    m.write_text("\n".join(lines) + "\n")
    _build(sub_root, tmp_path / "o", include_exploratory=True)
    assert ">recent<" not in (tmp_path / "o" / "index.html").read_text().split("<main")[1]


def test_public_words(root, tmp_path):
    _build(root, tmp_path / "o", include_exploratory=True)
    for n in ("index.html", "method.html", "submit.html"):
        page = (tmp_path / "o" / n).read_text()
        assert "<title>Hercyna – " in page and ">Hercyna<" in page
        low = page.lower()
        for bad in ("memory-bench", "rule l", "commit", "abstention"):
            assert bad not in low
    page = (tmp_path / "o" / "index.html").read_text()
    assert "pts" in page and "No rank" in page and "mock-oracle" in page
    assert "acts on" in page
    assert "wrong action" in page and "withheld" in page


def test_row_label_in_first_column(root, tmp_path):
    _build(root, tmp_path / "o")
    body = (tmp_path / "o" / "index.html").read_text().split("<main")[1]
    first = body.split('<tr class="row"')[1].split('class="c-b"')[0]
    assert "maintainer run" in first and ">bm25<" in first


def _pooled(root, tmp_path, monkeypatch, mutate=None):
    monkeypatch.setattr(site_build, "BASELINES", ("full",))
    if mutate:
        mutate(root / "results" / "runs" / "x1")
    mp = root / "results" / "runs" / "x1" / "manifest.json"
    man = json.loads(mp.read_text())
    man["grade"] = "table"
    mp.write_text(json.dumps(man))
    _build(root, tmp_path / "o")
    return (tmp_path / "o" / "index.html").read_text()


def test_pooling_same_cases_is_comparable(root, tmp_path, monkeypatch):
    page = _pooled(root, tmp_path, monkeypatch)
    assert 'data-verdict="runs not comparable"' not in page and 'class="verdict stop"' in page


def test_pooling_guard_cases_hash(root, tmp_path, monkeypatch):
    def mutate(d):
        mp = d / "manifest.json"
        man = json.loads(mp.read_text())
        man["cases"]["sha256"] = "0" * 64
        mp.write_text(json.dumps(man))
    assert 'data-verdict="runs not comparable"' in _pooled(root, tmp_path, monkeypatch, mutate)


def test_pooling_guard_gate_rows(root, tmp_path, monkeypatch):
    def mutate(d):
        m = d / "metrics.jsonl"
        out = []
        for line in m.read_text().splitlines():
            r = json.loads(line)
            if r["kind"] == "gate" and not out:
                r["action"] = "EXPLAIN" if r["action"] != "EXPLAIN" else "ABSTAIN"
            out.append(json.dumps(r, sort_keys=True, separators=(",", ":")))
        m.write_text("\n".join(out) + "\n")
    assert 'data-verdict="runs not comparable"' in _pooled(root, tmp_path, monkeypatch, mutate)


def test_cli_site_build(root, tmp_path, capsys):
    out = tmp_path / "dist"
    assert main(["site", "build", "--root", str(root), "--out", str(out), "--built-at", T]) == 0
    assert (out / "index.html").exists() and (out / "submit.html").exists()


def test_mdlite_subset():
    assert mdlite.render("## T\n\n1. a\n2. b\n\npara\nline") == (
        "<h2>T</h2>\n<ol><li>a</li><li>b</li></ol>\n<p>para line</p>")


def _cell(ex, ha, right):
    return {"headline": {"value": ex, "ci95": [ex - 0.01, ex + 0.01]},
            "abstention_above_floor": {"value": ha, "ci95": [ha - 0.01, ha + 0.01]},
            "right_action": {"value": right, "ci95": [right - 0.01, right + 0.01]}}


def _slot(cells: dict, label="maintainer run"):
    """deciders dict for the presentation helpers: {did: {(system, config, budget): (ex, ha, right)}}."""
    out = {}
    for did, rows in cells.items():
        out[did] = {"base": {"man": {}}, "floor": None, "rows": {
            k: {"key": k, "cell": _cell(*v), "also": [],
                "src": {"label": label if k[0] != "empty" else "exploratory", "dir": Path("/nonexistent"),
                        "man": {}, "sum": None, "id": "r"}}
            for k, v in rows.items()}}
    return out


def test_board_prints_no_rank_and_keeps_a_fixed_order():
    """Owner decision on review B1: no rank numbers, even with a leaderboard. Products A to Z, then baselines,
    empty after recent."""
    keys = {("mem0", "k25", 1000): (0.06, 0.39, 0.52), ("langmem", "c25", 1000): (0.15, 0.44, 0.38),
            ("graphiti", "e25", 1000): (0.10, 0.67, 0.20), ("empty", "default", 1000): (0.0, 0.9, 0.0),
            ("recent", "default", 1000): (0.0, 0.7, 0.3), ("bm25", "default", 1000): (0.1, 0.1, 0.8)}
    rows = site_build._board_data(_slot({"d": keys}))[0]["rows"]
    assert [r["system"] for r in rows] == ["graphiti", "langmem", "mem0", "bm25", "recent", "empty"]
    assert all("rank" not in r for r in rows)
    assert all('class="rank"' not in site_build._band(r, 0, i) for i, r in enumerate(rows))
    v = site_build._verdict_html({"leaderboard": True, "verdict": "leaderboard", "pairs": [("a | b", "a_lower")]})
    assert "Frozen verdict: leaderboard" in v and "a below b on wrong action" in v and 'class="verdict ok"' in v


def test_rank_status_names_the_separating_pairs():
    v = {"leaderboard": True, "verdict": "leaderboard", "alpha": 0.05, "by_decider": {},
         "pairs": [("graphiti/e25@1000 | langmem/c25@1000", "a_lower"), ("langmem/c25@1000 | mem0/k25@1000", "b_lower")]}
    assert site_build._pairs_line(v) == "Graphiti and Mem0 below LangMem on wrong action"
    k = site_build._kpis({}, v, {})
    assert "Rule met: 2 pairs separate" in k and "Graphiti and Mem0 below LangMem on wrong action" in k
    k = site_build._kpis({}, {"leaderboard": False, "verdict": "null", "pairs": []}, {})
    assert "No rank shown" in k and "No product pair separates" in k


def test_what_the_table_shows_is_generated_from_the_cells():
    """Dominance, the trade-off behind a separating pair, and the empty reference, with point estimates."""
    g, l, m, e = (("graphiti", "e25", 1000), ("langmem", "c25", 1000), ("mem0", "k25", 1000),
                  ("empty", "default", 1000))
    cells = {"d1": {g: (0.137, 0.625, 0.232), l: (0.249, 0.341, 0.403), m: (0.077, 0.365, 0.551), e: (0.0, 0.9, 0.0)},
             "d2": {g: (0.101, 0.675, 0.203), l: (0.155, 0.438, 0.384), m: (0.062, 0.394, 0.522), e: (0.0, 0.8, 0.0)}}
    v = {"leaderboard": True, "verdict": "leaderboard",
         "pairs": [("graphiti/e25@1000 | langmem/c25@1000", "a_lower"), ("langmem/c25@1000 | mem0/k25@1000", "b_lower")]}
    f = site_build._findings(_slot(cells), v)
    texts = [x["text"] for x in f]
    assert ("Mem0 is below Graphiti on wrong action and withheld action. It is above Graphiti on right action. "
            "This holds on both deciders, on point estimates.") in texts
    assert ("Graphiti has lower wrong action than LangMem on both deciders, but withholds more and is right less "
            "often.") in texts
    v["by_decider"] = {"d1": {"pairs": {"graphiti/e25@1000 | mem0/k25@1000": {"holm_p": 0.008, "separates": True}}},
                       "d2": {"pairs": {"graphiti/e25@1000 | mem0/k25@1000": {"holm_p": 0.0599, "separates": False}}}}
    texts = [x["text"] for x in site_build._findings(_slot(cells), v)]
    assert any("separates on d1 (Holm p 0.008); does not separate on d2 (Holm p 0.060)" in t for t in texts)
    # mixed withheld: more on d1, less on d2; right more often on both
    assert ("Mem0 has lower wrong action than LangMem on both deciders, but withholds more on d1 and withholds less "
            "on d2 and is right more often.") in texts
    assert sum(1 for x in f if x["kind"] == "dominance") == 1  # mem0 over graphiti only; empty is not a product
    empty = next(x for x in f if x["kind"] == "empty")
    assert "pure withholding" in empty["text"] and "exploratory" in empty["text"]
    assert empty["evidence"][0] == "d1: Empty excess wrong action +0.0 pts, withheld above floor +90.0 pts, right action 0.0%."
    g_l = next(x for x in f if x["text"].startswith("Graphiti has lower"))
    assert g_l["evidence"][1].startswith("d2: Graphiti excess wrong action +10.1 pts, withheld above floor +67.5 pts")
    html = site_build._findings_html(_slot(cells), v)
    assert "What the table shows" in html and "commit" not in html.lower() and "abstention" not in html.lower()
    assert site_build._findings_html({}, v) == ""


def test_row_config_line_reads_records_per_write():
    assert site_build._config_line("mem0", "k25", 1000) == "bench config · 25 records per write"
    assert site_build._config_line("langmem", "c40", 1000) == "bench config · 40 records per write"
    assert site_build._config_line("bm25", "default", 1000) == "default · 1000 tokens"
    assert site_build._config_line("acme", "vendor-tuned", 1000) == "bench config · vendor-tuned"


def test_exploratory_rows_from_the_released_table_folder(root, tmp_path):
    """An exploratory run released under results/table/<version>/ is public and labelled; one under
    results/runs/ stays hidden without the flag."""
    _mock_run(root / "results" / "table" / "v1" / "e1", "exploratory", "empty")
    _build(root, tmp_path / "o")
    body = (tmp_path / "o" / "index.html").read_text().split("<main")[1]
    assert ">empty<" in body and 'class="lab x"' in body and ">recent<" not in body
    assert body.index(">bm25<") < body.index(">empty<")


def test_limits_on_the_method_page(root, tmp_path):
    _build(root, tmp_path / "a")
    assert "site/copy/limits.md" in (tmp_path / "a" / "method.html").read_text()
    (root / "site" / "copy").mkdir(parents=True)
    (root / "site" / "copy" / "method.md").write_text("# Method\n\nThe method.\n")
    (root / "site" / "copy" / "limits.md").write_text("## What this does not show\n\nOne limit.\n")
    _build(root, tmp_path / "b")
    page = (tmp_path / "b" / "method.html").read_text()
    assert "<h2>What this does not show</h2>" in page and page.index("The method.") < page.index("One limit.")
    assert "copy pending" not in page.split("<main")[1].split("</main>")[0]


def test_empty_board(tmp_path):
    root = tmp_path / "root"
    (root / "protocol").mkdir(parents=True)
    shutil.copy(REPO / "protocol" / "v1.json", root / "protocol" / "v1.json")
    _build(root, tmp_path / "o")
    page = (tmp_path / "o" / "index.html").read_text()
    assert "No table run yet. The first full run prints here." in page
    assert 'class="verdict stop"' in page and 'data-verdict="no table run"' in page and "No table run yet." in page
    assert 'class="row"' not in page and 'class="tab"' not in page and 'class="frontier"' not in page
    sha = json.loads((REPO / "protocol" / "v1.json").read_text())["protocol_sha256"]
    assert f"protocol v1 {sha[:8]}" in page and "built 2026-11-19" in page


def test_static_board_without_script(root, tmp_path):
    """Every decider's rows, frontier and audits are in the HTML; script only adds tabs, filters and links."""
    _build(root, tmp_path / "o", include_exploratory=True)
    page = (tmp_path / "o" / "index.html").read_text()
    summ = json.loads((root / "results" / "runs" / "t1" / "summary.json").read_text())
    for did in summ["deciders"]:
        assert f'<h3 class="dhead nojs-only">{did}' in page
    assert page.count('<tr class="row"') >= 2 and 'style="width:' in page
    assert page.count('<svg class="frontier"') == len(summ["deciders"])
    assert '<div class="tab-rail js-only">' in page and 'id="f-base"' in page
    assert "document.documentElement.className+=' js'" in page
    assert '<div class="nojs-meta nojs-only">' in page and 'class="audit on"' in page


def test_rows_show_their_own_case_count(root, tmp_path):
    """x0 (4 cases) sorts before x1 (6 cases), so recent comes from x0 and bm25 from t1 (6 cases)."""
    rc = main(["run", "--mock", "--n", "4", "--adapters", "recent", "--budgets", "1000", "--repeats", "3",
               "--boot-reps", "50", "--out", str(root / "results" / "runs" / "x0")])
    assert rc == 0
    mp = root / "results" / "runs" / "x0" / "manifest.json"
    man = json.loads(mp.read_text())
    man["grade"] = "exploratory"
    mp.write_text(json.dumps(man))
    _build(root, tmp_path / "o", include_exploratory=True)
    page = (tmp_path / "o" / "index.html").read_text()
    bm25 = page.split('<span class="sys">bm25<')[1].split("</tr>")[0]
    recent = page.split('<span class="sys">recent<')[1].split("</tr>")[0]
    assert 'data-k="Cases"><b>6</b>' in bm25 and 'data-k="Cases"><b>4</b>' in recent


def test_exploratory_submission_shown_when_table_cases_exist(sub_root, tmp_path):
    """The repo holds data/table/cases.jsonl; an exploratory submission brings its own cases and must not be
    checked against the table cases (docs/submission.md section 3, check 4)."""
    (sub_root / "data" / "table").mkdir(parents=True)
    shutil.copy(REPO / "data" / "table" / "cases.jsonl", sub_root / "data" / "table" / "cases.jsonl")
    _build(sub_root, tmp_path / "b", include_exploratory=True)
    body = (tmp_path / "b" / "index.html").read_text().split("<main")[1]
    assert ">recent<" in body and 'class="lab x"' in body


def test_real_copy_files_keep_public_words(root, tmp_path):
    """The shipped copy in site/copy obeys the same public-word rule as the generated markup."""
    shutil.copytree(REPO / "site" / "copy", root / "site" / "copy", dirs_exist_ok=True)
    _build(root, tmp_path / "o")
    for n in ("index.html", "method.html", "submit.html"):
        low = (tmp_path / "o" / n).read_text().lower()
        assert "commit" not in low and "abstention" not in low and "rule l" not in low, n


def test_frontier_drawn_from_data(root, tmp_path):
    """One point per row at x = withheld above floor, y = excess wrong action, keyed to its row and audit."""
    _build(root, tmp_path / "o", include_exploratory=True)
    page = (tmp_path / "o" / "index.html").read_text()
    svg = page.split('<svg class="frontier"')[1].split("</svg>")[0]
    assert "Withheld above floor, points" in svg and "Excess wrong action, points" in svg
    assert '<g class="floor"' in svg
    summ = json.loads((root / "results" / "runs" / "t1" / "summary.json").read_text())
    cell = next(iter(next(iter(summ["deciders"].values()))["cells"].values()))
    ex, ha = cell["headline"]["value"] * 100, cell["abstention_above_floor"]["value"] * 100
    assert f'excess wrong action {ex:+.1f} pts' in svg and f'withheld above floor {ha:+.1f} pts' in svg
    assert 'class="pt" data-r="0"' in svg and 'class="row" data-d="0" data-r="0"' in page
    assert 'class="audit on" data-r="0"' in page


def test_frontier_positions_follow_values():
    from memory_bench.site import figures

    def row(s, x, y):
        return {"system": s, "base": False, "ex": {"t": f"{y:+.1f} pts", "v": y, "lo": y - 1, "hi": y + 1},
                "ha": {"t": f"{x:+.1f} pts", "v": x, "lo": x - 1, "hi": x + 1}}
    svg = figures.frontier_svg([row("a", 10.0, 2.0), row("b", 40.0, 8.0)], "d")
    import re
    cx = [float(v) for v in re.findall(r'<circle cx="([0-9.]+)"', svg)]
    cy = [float(v) for v in re.findall(r'<circle cx="[0-9.]+" cy="([0-9.]+)"', svg)]
    assert cx[1] > cx[0] and cy[1] < cy[0]  # more withheld is right, more wrong action is up


@pytest.mark.parametrize("verdict,expect", [
    (None, "No run has been verified"),
    ({"leaderboard": None, "verdict": "no products", "pairs": []}, "Baselines show the scale"),
    ({"leaderboard": None, "verdict": "needs two deciders", "pairs": []}, "product rows on both frozen deciders"),
    ({"leaderboard": None, "verdict": "runs not comparable", "pairs": []}, "same case set"),
])
def test_why_no_rank_follows_reason(verdict, expect):
    vals = json.loads((REPO / "protocol" / "v1.json").read_text())["steps"]["1"]["values"]
    v = site_build._verdict_html(verdict, vals)
    key = "no table run" if verdict is None else verdict["verdict"]
    assert f'data-verdict="{key}"' in v and expect in v and "Why no rank is published" in v
    assert f'alpha {vals["separation"]["alpha"]}' in v and "Holm p" not in v


def test_why_no_rank_prints_pair_results():
    pair = {"value": -0.031, "ci95": [-0.08, 0.02], "p": 0.2, "holm_p": 0.41, "separates": False,
            "direction": "a_lower"}
    v = {"leaderboard": False, "verdict": "null", "pairs": [], "alpha": 0.05,
         "by_decider": {"d1": {"pairs": {"x/k@1000 | y/k@1000": pair}}, "d2": {"pairs": {"x/k@1000 | y/k@1000": pair}}}}
    h = site_build._verdict_html(v, {})
    assert 'data-verdict="null"' in h and "no product pair separates on both deciders" in h.lower()
    assert "-3.1 pts [-8.0, 2.0]" in h and "0.410" in h and "Holm p" in h and h.count("<h3>d") == 2


def test_case_anatomy_uses_a_dev_case(root, tmp_path):
    from memory_bench.cases.sample import dev_cases

    _build(root, tmp_path / "o")
    page = (tmp_path / "o" / "index.html").read_text()
    sec = page.split('id="anatomy"')[1].split("</section>")[0]
    case = next(c for c in dev_cases() if c.mechanism == "M3")
    assert "Synthetic development case" in sec and case.case_id in sec and case.a_star in sec
    for d in case.d_star:
        assert f"({d})" in sec
    import re
    assert not re.search(r"\bt\d+-\d{4}", page)  # never a table case id (t<seed>-NNNN)
    table = (REPO / "data" / "table" / "cases.jsonl")
    if table.is_file():
        ids = {json.loads(l)["case_id"] for l in table.read_text().splitlines() if l.strip()}
        assert case.case_id not in ids


def test_theme_toggle_markup(root, tmp_path):
    _build(root, tmp_path / "o")
    for n in ("index.html", "method.html", "submit.html"):
        page = (tmp_path / "o" / n).read_text()
        assert '<button class="btn js-only" type="button" id="theme" aria-pressed="false">' in page
        assert "localStorage.getItem('hercyna-theme')" in page and "try{" in page
        assert "localStorage.setItem('hercyna-theme'" in page
        assert '@media (prefers-color-scheme: dark) { :root:not([data-theme="light"])' in page
        assert ':root[data-theme="dark"]' in page


def test_media_copied_and_marked(root, tmp_path):
    res = _build(root, tmp_path / "o")
    media = REPO / "site" / "media"
    if not media.is_dir() or not (media / "how-light.mp4").exists():
        pytest.skip("no reel media (the public release ships without the generated reel)")
    page = (tmp_path / "o" / "index.html").read_text()
    for name in ("how-light.mp4", "how-dark.mp4", "how-light.jpg", "how-dark.jpg"):
        assert (tmp_path / "o" / "media" / name).read_bytes() == (media / name).read_bytes()
        assert f"media/{name}" in res["bytes"]
        assert b"FNU Ratnavarma @notebook.beer" in (media / name).read_bytes()
    assert 'preload="none"' in page and "autoplay" not in page and page.count("<video ") == 2
    assert all(" muted " in v for v in page.split("<video ")[1:])


def test_about_page_and_why_section(root, tmp_path):
    """About page from site/copy/about.md; Why Hercyna section on the board page; content rules hold."""
    shutil.copytree(REPO / "site" / "copy", root / "site" / "copy", dirs_exist_ok=True)
    _build(root, tmp_path / "o")
    about = (tmp_path / "o" / "about.html").read_text()
    index = (tmp_path / "o" / "index.html").read_text()
    assert "Who builds Hercyna" in about and "orcid.org/0009-0008-2191-6575" in about
    why = (tmp_path / "o" / "why.html").read_text()
    assert "Lethe" in why and "Mnemosyne" in why and 'href="about.html"' in why
    assert 'href="why.html"' in index and 'href="why.html"' in about  # its own page, linked from the rail
    assert "thinkandreason.org" in about
    for page in (about, index, why):
        low = page.lower()
        local = REPO / "banned_words.local.txt"  # private names, never committed (also read by test_generator)
        names = [w.strip().lower() for w in local.read_text().splitlines() if w.strip()] if local.exists() else []
        for banned in (*names, "phd", "published in"):
            assert banned not in low, banned


def test_rank_tiers_follow_the_separation_rule():
    """Ranks come from separating pairs, ties share a rank, and only products are ranked."""
    from memory_bench.site.build import _tiers

    v = {"pairs": [("graphiti/e25@1000 | langmem/c25@1000", "a_lower"),
                   ("langmem/c25@1000 | mem0/k25@1000", "b_lower")]}
    t = _tiers(v)
    assert t == {"langmem/c25@1000": 3}  # two products separate below it
    assert t.get("mem0/k25@1000", 1) == t.get("graphiti/e25@1000", 1) == 1  # not separated: shared rank


def test_anchor_links_are_not_left_to_smooth_scroll(root, tmp_path):
    """Global smooth scrolling was cancelled by the stepper's layout changes, so rail links went nowhere.
    Anchors jump instantly, and a page opened with #section aims again once fonts are ready."""
    _build(root, tmp_path / "o")
    page = (tmp_path / "o" / "index.html").read_text()
    assert "scroll-behavior: smooth" not in page and "scroll-behavior:smooth" not in page
    assert "behavior: 'instant'" in page


def test_memory_cost_comes_from_the_ingesting_run(tmp_path):
    """Grok product runs reuse the Haiku runs' stored views and meter no memory side; the per-case cost must still
    include the ingest, read from the run that paid it."""
    from memory_bench.site import build as b

    b.MEMORY_COST.clear()
    b.MEMORY_COST["graphiti"] = 0.30
    d = tmp_path / "r"
    d.mkdir()
    (d / "spend.json").write_text(json.dumps({"by_adapter": {"graphiti": {"usd": 3.0, "memory_side": {"usd": 0.0}}}}))
    total, split = b._usd_per_case({"dir": d, "man": {"cases": {"n": 100}}}, "graphiti")
    assert round(total, 3) == 0.33 and split == (0.30, 0.03)
    b.MEMORY_COST.clear()
