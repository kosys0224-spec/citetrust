"""citetrust tests - all offline (recorded responses in tests/fixtures via tests/replay.py)."""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from replay import replay_fetch  # noqa: E402
from citetrust import cache, sources  # noqa: E402
from citetrust.cli import main  # noqa: E402
from citetrust.extract import extract_bibtex, extract_from_text, extract_latex_bibitems, extract_references  # noqa: E402
from citetrust.model import Record, Reference, Status  # noqa: E402
from citetrust.report import render_json, render_markdown, render_sarif, render_text  # noqa: E402
from citetrust.verify import compare, title_similarity, verify_one, verify_references  # noqa: E402

SAMPLE = ROOT / "examples" / "sample.md"


def by_key(results):
    return {r.ref.key: r for r in results}


class TestExtraction(unittest.TestCase):
    def test_sample_markdown(self):
        refs = extract_references(SAMPLE)
        self.assertEqual(len(refs), 8)
        k = {r.key: r for r in refs}
        self.assertEqual(k["1"].doi, "10.1109/5.771073")
        self.assertEqual(k["1"].title, "Toward unique identifiers")
        self.assertEqual(k["1"].year, 1999)
        self.assertEqual(k["1"].authors[:1], ["Paskin"])
        self.assertEqual(k["1"].container, "Proceedings of the IEEE")
        self.assertEqual(k["2"].arxiv, "1706.03762")
        self.assertEqual(k["5"].doi, "10.1016/S0140-6736(97)11096-0")  # parentheses kept
        self.assertEqual(k["6"].url, "https://www.example.org/entsoe/fcr-requirements-2023")
        self.assertEqual(k["7"].title, "Voltage sag mitigation in rural feeders using community batteries")
        self.assertIn("Kim", k["7"].authors)
        self.assertEqual(k["4"].doi, k["1"].doi)  # same DOI cited twice is kept (that is the point)
        self.assertTrue(all(r.line for r in refs))

    def test_bibtex(self):
        bib = """@article{vaswani2017,
  title = {Attention is all you need},
  author = {Vaswani, Ashish and Shazeer, Noam},
  year = {2017},
  journal = {NeurIPS},
  eprint = {1706.03762},
}
@book{knuth, title="The {TeX}book", author="Donald E. Knuth", year=1984, publisher="Addison-Wesley", doi={10.1000/xyz}}
@comment{ignored}
"""
        refs = extract_bibtex(bib)
        self.assertEqual([r.key for r in refs], ["vaswani2017", "knuth"])
        self.assertEqual(refs[0].arxiv, "1706.03762")
        self.assertEqual(refs[0].authors, ["Vaswani", "Shazeer"])
        self.assertEqual(refs[0].container, "NeurIPS")
        self.assertEqual(refs[1].title, "The TeXbook")
        self.assertEqual(refs[1].authors, ["Knuth"])
        self.assertEqual(refs[1].doi, "10.1000/xyz")
        self.assertEqual(refs[1].year, 1984)

    def test_latex_bibitems(self):
        tex = r"""\begin{thebibliography}{9}
\bibitem{pask} N. Paskin, \emph{Toward unique identifiers}, Proc. IEEE, 1999. doi:10.1109/5.771073
\bibitem{fake} J. Smith, ``A paper that does not exist,'' Journal of Nothing, 2022.
\end{thebibliography}"""
        refs = extract_latex_bibitems(tex)
        self.assertEqual([r.key for r in refs], ["pask", "fake"])
        self.assertEqual(refs[0].doi, "10.1109/5.771073")
        self.assertEqual(refs[1].year, 2022)

    def test_docx(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "paper.docx"
            doc = ('<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
                   '<w:p><w:r><w:t>References</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>[1] Paskin, N. (1999). Toward unique </w:t></w:r><w:r><w:t>identifiers. Proceedings of the IEEE. https://doi.org/10.1109/5.771073</w:t></w:r></w:p>'
                   '<w:p><w:r><w:t>[2] Kim, H. (2020). Voltage sag mitigation in rural feeders using community batteries. J. Rural Power.</w:t></w:r></w:p>'
                   '</w:body></w:document>')
            with zipfile.ZipFile(p, "w") as zf:
                zf.writestr("[Content_Types].xml", "<Types/>")
                zf.writestr("word/document.xml", doc)
            refs = extract_references(p)
        self.assertEqual(len(refs), 2)
        self.assertEqual(refs[0].doi, "10.1109/5.771073")
        self.assertEqual(refs[0].title, "Toward unique identifiers")
        self.assertEqual(refs[1].year, 2020)

    def test_inline_identifiers_without_bibliography(self):
        text = "See doi:10.1109/5.771073 and arXiv:1706.03762v5 and PMID: 31452104, plus https://example.com/page.\nNothing else here."
        refs = extract_from_text(text)
        kinds = {r.kind for r in refs}
        self.assertEqual(kinds, {"doi", "arxiv", "pmid", "url"})
        self.assertEqual(next(r for r in refs if r.kind == "arxiv").arxiv, "1706.03762")

    def test_html(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "post.html"
            p.write_text("<h2>References</h2><p>[1] Paskin, N. (1999). Toward unique identifiers. <a href=\"https://doi.org/10.1109/5.771073\">doi</a></p>", encoding="utf-8")
            refs = extract_references(p)
        self.assertEqual(len(refs), 1)
        self.assertEqual(refs[0].doi, "10.1109/5.771073")

    def test_bibtex_from_text_autodetect(self):
        refs = extract_from_text("@misc{x, title={Hello world paper}, year={2020}}")
        self.assertEqual(refs[0].kind, "bibtex")


class TestCompare(unittest.TestCase):
    def test_title_similarity(self):
        self.assertEqual(title_similarity("Attention is all you need", "Attention Is All You Need"), 1.0)
        self.assertGreater(title_similarity("Toward unique identifiers", "Towards unique identifiers."), 0.85)
        self.assertLess(title_similarity("Toward unique identifiers", "Digital object identifiers for scientific data sets"), 0.6)
        self.assertIsNone(title_similarity(None, "x"))

    def test_compare_mismatch_and_ok(self):
        rec = Record("crossref", "10.1/x", "Toward unique identifiers", ["Paskin"], 1999)
        ok, reasons, _ = compare(Reference(raw="", title="Toward unique identifiers", authors=["Paskin"], year=1999), rec)
        self.assertTrue(ok)
        self.assertEqual(reasons, [])
        ok, reasons, _ = compare(Reference(raw="", title="Something else entirely here", authors=["Smith"], year=2010), rec)
        self.assertFalse(ok)
        self.assertEqual(len(reasons), 3)
        ok, reasons, _ = compare(Reference(raw="", title="Toward unique identifiers", year=2000), rec)
        self.assertTrue(ok)
        self.assertIn("off by one", reasons[0])
        ok, _, _ = compare(Reference(raw=""), rec)
        self.assertIsNone(ok)


class TestVerify(unittest.TestCase):
    def test_sample_end_to_end(self):
        refs = extract_references(SAMPLE)
        results = by_key(verify_references(refs, replay_fetch, workers=4))
        self.assertEqual(results["1"].status, Status.VERIFIED)
        self.assertEqual(results["2"].status, Status.VERIFIED)
        self.assertEqual(results["3"].status, Status.NOT_FOUND)
        self.assertEqual(results["4"].status, Status.MISMATCH)
        self.assertEqual(results["5"].status, Status.RETRACTED)
        self.assertEqual(results["6"].status, Status.DEAD_LINK)
        self.assertTrue(any("web.archive.org" in s for s in results["6"].suggestions))
        self.assertEqual(results["7"].status, Status.NOT_FOUND)
        self.assertEqual(results["8"].status, Status.VERIFIED)
        self.assertTrue(any("10.1109/pesgm40551.2019.8973939" in s for s in results["8"].suggestions))

    def test_doi_without_crossref_falls_back_to_openalex(self):
        r = verify_one(Reference(raw="", doi="10.5281/zenodo.1234567", title="A dataset of synthetic distribution feeders", year=2021), replay_fetch)
        self.assertEqual(r.status, Status.VERIFIED)
        self.assertEqual(r.record.source, "openalex")

    def test_arxiv_missing_and_pmid(self):
        r = verify_one(Reference(raw="", arxiv="9999.99999"), replay_fetch)
        self.assertEqual(r.status, Status.NOT_FOUND)
        r = verify_one(Reference(raw="", pmid="31452104", title="Deep learning in power systems research: a review", year=2019), replay_fetch)
        self.assertEqual(r.status, Status.VERIFIED)
        r = verify_one(Reference(raw="", pmid="1"), replay_fetch)
        self.assertEqual(r.status, Status.NOT_FOUND)

    def test_urls(self):
        self.assertEqual(verify_one(Reference(raw="", url="https://example.com/alive"), replay_fetch).status, Status.VERIFIED)
        self.assertEqual(verify_one(Reference(raw="", url="https://forbidden.example/x"), replay_fetch).status, Status.UNREACHABLE)
        self.assertEqual(verify_one(Reference(raw="", url="https://unreachable.invalid/x"), replay_fetch).status, Status.UNREACHABLE)
        self.assertEqual(verify_one(Reference(raw="", url="https://example.com/x"), replay_fetch, check_urls=False).status, Status.SKIPPED)

    def test_network_error_is_unreachable(self):
        def boom(url, method="GET"):
            raise sources.NetworkError("offline")
        r = verify_one(Reference(raw="", doi="10.1109/5.771073"), boom)
        self.assertEqual(r.status, Status.UNREACHABLE)


class TestReportsAndCli(unittest.TestCase):
    def run_cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = main(list(args), fetch=replay_fetch)
        return code, out.getvalue(), err.getvalue()

    def test_text_json_markdown_sarif(self):
        refs = extract_references(SAMPLE)
        results = verify_references(refs, replay_fetch)
        text = render_text(results, color=False)
        self.assertIn("RETRACTD", text)
        self.assertIn("8 checked", text)
        doc = json.loads(render_json(results))
        self.assertEqual(doc["summary"]["retracted"], 1)
        md = render_markdown(results)
        self.assertIn("| **retracted** |", md)
        sarif = json.loads(render_sarif(results))
        self.assertEqual(sarif["version"], "2.1.0")
        self.assertEqual(len(sarif["runs"][0]["results"]), 5)

    def test_cli_exit_codes(self):
        code, out, _ = self.run_cli(str(SAMPLE), "--no-color")
        self.assertEqual(code, 1)
        self.assertIn("MISMATCH", out)
        code, _, _ = self.run_cli(str(SAMPLE), "--fail-on", "never")
        self.assertEqual(code, 0)
        code, _, _ = self.run_cli(str(SAMPLE), "--fail-on", "retracted", "--json")
        self.assertEqual(code, 1)
        code, out, err = self.run_cli(str(SAMPLE), "--list")
        self.assertEqual(code, 0)
        self.assertIn("8 reference(s)", err)
        code, _, err = self.run_cli(str(ROOT / "nope.md"))
        self.assertEqual(code, 2)
        code, out, _ = self.run_cli(str(SAMPLE), "--only-problems", "--no-color")
        self.assertNotIn(" OK ", out)

    def test_cli_directory_and_empty(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "a.md").write_text("# Notes\n\nNothing cited here.\n", encoding="utf-8")
            code, _, err = self.run_cli(d)
            self.assertEqual(code, 0)
            self.assertIn("no references found", err)

    def test_cache_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            os.environ["CITETRUST_CACHE_DIR"] = d
            try:
                calls = []

                def counting(url, method="GET"):
                    calls.append(url)
                    return replay_fetch(url, method)

                f = cache.cached_fetcher(counting)
                f("https://api.crossref.org/works/10.1109%2F5.771073")
                f("https://api.crossref.org/works/10.1109%2F5.771073")
                self.assertEqual(len(calls), 1)
                self.assertEqual(cache.clear(), 1)
            finally:
                del os.environ["CITETRUST_CACHE_DIR"]


if __name__ == "__main__":
    unittest.main()
