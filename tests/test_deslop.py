from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills" / "deslop-ai"
sys.path.insert(0, str(SKILL / "scripts"))

from build_fixtures import build
from deslop_core import Analyzer, DeslopError, PRIVATE_ROOT, protected_equal, sha256_file, strict_json_load, validate_request
from formats import extract_source


def request(input_data: dict, operation: str = "audit") -> dict:
    return {
        "schemaVersion": "deslop-request/v1", "id": "synthetic-test", "prompt": "Audit this synthetic fixture.",
        "input": input_data, "operation": operation, "genre": "consulting", "profileId": "none",
        "communicationJob": {"audience": "test reviewers", "outcome": "verify behavior", "takeaway": "The tool preserves facts and rejects empty words."},
        "rewritePolicy": "conservative", "preserveTerms": ["EUR"],
    }


class TestDeSlop(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixtures = ROOT / "tests" / "fixtures"
        required = [cls.fixtures / name for name in ["sample.txt", "sample.md", "sample.docx", "sample.pptx", "sample.pdf"]]
        if not all(path.exists() for path in required):
            build()
        cls.analyzer = Analyzer()

    def test_catalog_positive_cases_at_least_50(self) -> None:
        terms = []
        for group in self.analyzer.lex_groups:
            terms.extend(str(term) for term in group.get("terms", []))
        self.assertGreaterEqual(len(terms), 50)
        for index, term in enumerate(terms[:50]):
            text = f"This passage mentions {term} in a generic way."
            block = {"blockId": f"b{index}", "locator": f"test:{index}", "text": text, "sourceHash": "h", "scope": "test", "role": "paragraph"}
            self.assertTrue(self.analyzer.analyze_block(block, "general"), term)

    def test_negative_controls_do_not_trigger_high_severity_value_accusation(self) -> None:
        controls = [
            "Revenue increased 14% after the Berlin team changed pricing.",
            "The board rejected Option B because it costs EUR 2 million more.",
            "Smith (2024) reports a 6-point improvement in the treatment group.",
            "We launched the trial on 12 March and enrolled 81 patients.",
            "Cycle time fell from 9 days to 4 days.",
        ] * 5
        blocks = [{"blockId": f"n{i}", "locator": f"negative:{i}", "text": text, "sourceHash": str(i), "scope": f"s{i}", "role": "paragraph"} for i, text in enumerate(controls)]
        _, assessments = self.analyzer.analyze(blocks, "general")
        self.assertEqual(25, len(assessments))
        self.assertFalse(any(item["verdict"] == "needs-improvement" for item in assessments))

    def test_every_block_value_coverage(self) -> None:
        source = extract_source(request({"kind": "file", "path": str(self.fixtures / "sample.pptx")}))
        blocks = [block for block in source["blocks"] if block["eligible"]]
        _, assessments = self.analyzer.analyze(blocks, "consulting")
        self.assertEqual(len(blocks), len(assessments))
        self.assertEqual({b["blockId"] for b in blocks}, {a["blockId"] for a in assessments})
        self.assertTrue(any(a["verdict"] == "needs-improvement" for a in assessments))

    def test_protected_tokens(self) -> None:
        before = 'Revenue was 14% (Smith, 2024); see https://example.com and "keep this quote".'
        after = 'Revenue reached 14% (Smith, 2024); see https://example.com and "keep this quote".'
        self.assertTrue(protected_equal(before, after, ["Revenue"])[0])
        self.assertFalse(protected_equal(before, after.replace("14%", "15%"), ["Revenue"])[0])

    def test_strict_request_rejects_unknown_and_duplicates(self) -> None:
        data = request({"kind": "text", "text": "Hello"})
        data["threshold"] = 5
        with self.assertRaises(DeslopError):
            validate_request(data)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "duplicate.json"; path.write_text('{"a": 1, "a": 2}', encoding="utf-8")
            with self.assertRaises(DeslopError):
                strict_json_load(path)

    def test_text_guarded_run_and_independent_verify(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td); req = request({"kind": "file", "path": str(self.fixtures / "sample.txt")}, "audit-and-rewrite")
            request_path = temp / "request.json"; request_path.write_text(json.dumps(req), encoding="utf-8")
            original = sha256_file(self.fixtures / "sample.txt")
            run = temp / "run"
            call = subprocess.run([sys.executable, str(SKILL / "scripts" / "deslop.py"), "request", str(request_path), "--out", str(run)], capture_output=True, text=True)
            self.assertEqual(0, call.returncode, call.stderr)
            self.assertEqual(original, sha256_file(self.fixtures / "sample.txt"))
            receipt = json.loads((run / "verification.json").read_text(encoding="utf-8"))
            self.assertEqual(receipt["eligibleBlockCount"], receipt["assessedBlockCount"])
            self.assertTrue((run / "value-coverage.json").exists())
            self.assertTrue((run / "sample-deslopped.txt").exists())
            external = temp / "external.json"
            verify = subprocess.run([sys.executable, str(SKILL / "scripts" / "deslop.py"), "request-verify", str(run), "--out", str(external)], capture_output=True, text=True)
            self.assertEqual(0, verify.returncode, verify.stderr)

    def test_format_extractors_and_pdf_audit_policy(self) -> None:
        for name in ["sample.docx", "sample.pptx", "sample.pdf", "sample.md"]:
            source = extract_source(request({"kind": "file", "path": str(self.fixtures / name)}))
            self.assertIn("blocks", source)
            self.assertIn("invariants", source)
        pdf = extract_source(request({"kind": "file", "path": str(self.fixtures / "sample.pdf")}))
        self.assertEqual("pdf", pdf["kind"])
        self.assertTrue(all(not b["supportedForRewrite"] for b in pdf["blocks"]))

    def test_markdown_context_and_numeric_range_are_not_artifacts(self) -> None:
        source = extract_source(request({"kind": "file", "path": str(self.fixtures / "sample.md")}))
        findings, _ = self.analyzer.analyze(source["blocks"], "general")
        ids = {item["ruleId"] for item in findings}
        self.assertNotIn("ART_MARKDOWN_HEADING", ids)
        self.assertNotIn("STR_RANGE_FROM_TO", ids)

    def test_docx_and_pptx_guarded_revisions_open_in_office(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            temp = Path(td)
            for name in ["sample.docx", "sample.pptx"]:
                req = request({"kind": "file", "path": str(self.fixtures / name)}, "audit-and-rewrite")
                req["id"] = f"synthetic-{Path(name).stem}"
                request_path = temp / f"{name}.json"; request_path.write_text(json.dumps(req), encoding="utf-8")
                run = temp / f"run-{Path(name).suffix.lstrip('.')}"
                original = sha256_file(self.fixtures / name)
                call = subprocess.run([sys.executable, str(SKILL / "scripts" / "deslop.py"), "request", str(request_path), "--out", str(run)], capture_output=True, text=True, timeout=90)
                self.assertEqual(0, call.returncode, f"{name}: {call.stderr}\n{call.stdout}")
                self.assertEqual(original, sha256_file(self.fixtures / name))
                receipt = json.loads((run / "verification.json").read_text(encoding="utf-8"))
                self.assertTrue(receipt["passed"])
                self.assertTrue((run / f"sample-deslopped{Path(name).suffix}").exists())
                plan = json.loads((run / "rewrite-plan.json").read_text(encoding="utf-8"))
                self.assertTrue(any("mixed run formatting" in item["reason"].casefold() for item in plan["abstentions"]))

    def test_private_profile_stores_metrics_not_text(self) -> None:
        PRIVATE_ROOT.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=PRIVATE_ROOT) as td:
            parent = Path(td); profile_request = {
                "schemaVersion": "deslop-profile-request/v1", "id": "consulting-test", "genre": "consulting",
                "inputs": [str(self.fixtures)], "approvedExcerptIds": [],
            }
            request_path = parent / "profile-request-input.json"; request_path.write_text(json.dumps(profile_request), encoding="utf-8")
            output = parent / "profile"
            call = subprocess.run([sys.executable, str(SKILL / "scripts" / "deslop.py"), "profile", str(request_path), "--out", str(output)], capture_output=True, text=True)
            self.assertEqual(0, call.returncode, call.stderr)
            profile = json.loads((output / "profile.json").read_text(encoding="utf-8"))
            self.assertFalse(profile["privacy"]["rawTextStored"])
            serialized = json.dumps(profile)
            self.assertNotIn("Unlocking Transformative Excellence", serialized)


if __name__ == "__main__":
    unittest.main()
