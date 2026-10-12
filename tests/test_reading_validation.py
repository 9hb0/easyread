"""精读验证、范围导航和并发写入的回归测试；不调用模型。"""
import copy
import tempfile
import unittest
import uuid
from pathlib import Path

from easyread import reading
from easyread.paperdata import merge_blocks, set_block_text
from easyread.store import Workspace, write_json_atomic


def block(bid="p1-1", page=1, **fields):
    return {"id": bid, "page": page, "type": "para",
            "source_en": "We propose a new method. Experiments show a gain of 2%.",
            "zh": "我们提出新方法。实验证明提高2%。", **fields}


def ref(bid="p1-1", page=1, quote="a new method"):
    return {"page": page, "block": bid, "source_quote": quote}


def insight(iid="i1", bid="p1-1", page=1, **fields):
    return {"id": iid, "type": "method", "content": "提出新方法。",
            "block": bid, "page": page, "status": "provisional",
            "source_refs": [ref(bid, page)], **fields}


def paper(total=3, done=(1,), **translation):
    return {"meta": {"page_count": total}, "blocks": [block(f"p{p}-1", p) for p in done],
            "translation": {"done_pages": list(done),
                            "source_versions": {str(p): f"source-{p}" for p in done}, **translation},
            "reading": {"insights": [], "pages": {}}}


class HighlightsValidationTest(unittest.TestCase):
    def test_occurrence_accepts_only_real_integers(self):
        for occurrence in (True, False, "0", 0.0, 1.5, None, -1):
            with self.subTest(occurrence=occurrence):
                b = block(highlights=[{"category": "method", "quote": "新方法", "occurrence": occurrence}])
                self.assertTrue(reading.validate_highlights([b]))
                self.assertNotIn("highlights", b)

    def test_locate_quote_rejects_invalid_input_without_coercion(self):
        for occurrence in (True, "0", 0.0, None):
            with self.subTest(occurrence=occurrence):
                self.assertIsNone(reading.locate_quote("新方法", "新方法", occurrence))

    def test_exact_quotes_and_owned_block_ids_are_retained(self):
        b = block(zh="😀 新方法\n 新方法", highlights=[
            {"category": "method", "quote": " 新方法", "occurrence": 1}])
        self.assertEqual(reading.validate_highlights([b]), [])
        self.assertEqual(b["highlights"], [{"id": "h-p1-1-0", "block_id": "p1-1",
                                          "category": "method", "quote": " 新方法", "occurrence": 1}])
        self.assertEqual(reading.validate_highlights([b]), [])

    def test_explicit_wrong_block_id_is_rejected(self):
        b = block(highlights=[{"block_id": "p2-1", "category": "method", "quote": "新方法"}])
        self.assertTrue(reading.validate_highlights([b]))
        self.assertNotIn("highlights", b)

    def test_duplicate_highlights_are_deduplicated_without_conflict(self):
        h = {"category": "method", "quote": "新方法", "occurrence": 0}
        b = block(highlights=[h, dict(h)])
        self.assertEqual(reading.validate_highlights([b]), [])
        self.assertEqual(len(b["highlights"]), 1)

    def test_all_conflicting_highlights_are_hidden_regardless_of_order(self):
        conflicts = [{"category": "method", "quote": "提出新方法"},
                     {"category": "innovation", "quote": "新方法"},
                     {"category": "problem", "quote": "我们提出"}]
        for order in (conflicts, conflicts[::-1]):
            with self.subTest(order=order):
                b = block(highlights=order + [{"category": "evidence", "quote": "提高2%"}])
                self.assertTrue(reading.validate_highlights([b]))
                self.assertEqual([h["quote"] for h in b["highlights"]], ["提高2%"])

    def test_quotes_cannot_touch_or_cross_math(self):
        for text, quote in (("前$x+y$后", "前$x+y$后"), ("前$$x$$后", "x"),
                            (r"前\(x\)后", r"\(x\)"), (r"前\[x\]后", r"前\[x\]"),
                            ("前$x$后", "前$")):
            with self.subTest(text=text):
                b = block(zh=text, highlights=[{"category": "method", "quote": quote}])
                self.assertTrue(reading.validate_highlights([b]))
                self.assertNotIn("highlights", b)

    def test_quotes_inside_a_list_item_work_but_cannot_cross_items(self):
        b = block(type="list", items=[{"zh": "第一条"}, {"zh": "第二条"}], highlights=[
            {"category": "method", "quote": "第一条\n第二条"},
            {"category": "evidence", "quote": "第二条"}])
        self.assertTrue(reading.validate_highlights([b]))
        self.assertEqual([h["quote"] for h in b["highlights"]], ["第二条"])

    def test_malformed_categories_and_quotes_do_not_crash_validation(self):
        b = block(zh="123", highlights=[{"category": [], "quote": "123"},
                                       {"category": "method", "quote": 123}])
        self.assertTrue(reading.validate_highlights([b]))
        self.assertNotIn("highlights", b)


class ReferencesValidationTest(unittest.TestCase):
    def annotation(self, **fields):
        return {"type": "method_explanation", "content": "这里解释文中的方法。",
                "basis": "source_supported", "anchor_quote": "新方法", "source_refs": [ref()], **fields}

    def test_annotations_keep_block_id_occurrence_and_exact_refs_idempotently(self):
        b = block(annotations=[self.annotation()])
        self.assertEqual(reading.validate_annotations([b]), [])
        ann = b["annotations"][0]
        self.assertEqual(ann["block_id"], "p1-1")
        self.assertEqual(ann["occurrence"], 0)
        self.assertEqual(ann["source_refs"], [ref()])
        before = copy.deepcopy(b)
        self.assertEqual(reading.validate_annotations([b]), [])
        self.assertEqual(b, before)

    def test_annotation_legacy_owned_block_refs_are_compatible(self):
        ann = self.annotation(basis="ai_explanation")
        ann.pop("source_refs")
        b = block(annotations=[ann])
        self.assertEqual(reading.validate_annotations([b]), [])
        self.assertEqual(b["annotations"][0]["source_refs"], [{"page": 1, "block": "p1-1"}])

    def test_annotation_wrong_block_or_occurrence_is_rejected(self):
        for fields in ({"block_id": "p2-1"}, {"occurrence": True}, {"occurrence": "0"}, {"occurrence": 0.5}):
            with self.subTest(fields=fields):
                b = block(annotations=[self.annotation(**fields)])
                self.assertTrue(reading.validate_annotations([b]))
                self.assertNotIn("annotations", b)

    def test_annotations_cannot_cross_math_or_list_boundaries(self):
        fixtures = [block(zh="前$x$后", annotations=[self.annotation(anchor_quote="前$x$后")]),
                    block(type="list", items=[{"zh": "第一条"}, {"zh": "第二条"}],
                          annotations=[self.annotation(anchor_quote="第一条\n第二条")])]
        for b in fixtures:
            with self.subTest(block=b):
                self.assertTrue(reading.validate_annotations([b]))
                self.assertNotIn("annotations", b)

    def test_refs_require_existing_page_block_and_an_exact_english_quote(self):
        invalid = [ref(page=2), ref(bid="missing"), ref(quote="新方法"), ref(quote="a  new method"),
                   ref(quote="invented result"), {"page": 2, "block": "p2-1"}]
        for source_ref in invalid:
            with self.subTest(ref=source_ref):
                b = block(annotations=[self.annotation(source_refs=[source_ref])])
                self.assertTrue(reading.validate_annotations([b, block("p2-1", 2)]))
                self.assertNotIn("annotations", b)

    def test_refs_use_raw_source_en_instead_of_model_generated_en(self):
        b = block(en="The imaginary invention is revolutionary.",
                  annotations=[self.annotation(source_refs=[ref(quote="imaginary invention")])])
        self.assertTrue(reading.validate_annotations([b]))
        self.assertNotIn("annotations", b)

    def test_quote_alias_and_cross_block_refs_are_valid(self):
        evidence = block("p2-1", 2, source_en="The accuracy improves by 2%.")
        source_ref = {"page": 2, "block": "p2-1", "quote": "accuracy improves by 2%"}
        b = block(annotations=[self.annotation(source_refs=[source_ref])])
        self.assertEqual(reading.validate_reading([b, evidence]), [])
        self.assertEqual(b["annotations"][0]["source_refs"], [source_ref])

    def test_supported_insights_require_quoted_refs(self):
        raw = [{"type": "evidence", "content": "提高2%。", "block": "p1-1", "status": "supported"}]
        clean, _ = reading.validate_insights(raw, [block()])
        self.assertEqual(clean[0]["status"], "provisional")
        self.assertEqual(clean[0]["source_refs"], [{"page": 1, "block": "p1-1"}])

    def test_supported_insights_preserve_their_valid_refs(self):
        clean, problems = reading.validate_insights([insight(status="supported")], [block()])
        self.assertEqual(problems, [])
        self.assertEqual(clean[0]["status"], "supported")
        self.assertEqual(clean[0]["source_refs"], [ref()])

    def test_ref_only_insights_can_derive_their_owned_block(self):
        raw = {"type": "method", "content": "提出新方法。", "source_refs": [ref()], "status": "supported"}
        clean, problems = reading.validate_insights([raw], [block()])
        self.assertEqual(problems, [])
        self.assertEqual(clean[0]["block"], "p1-1")
        self.assertEqual(clean[0]["page"], 1)

    def test_invalid_insight_refs_are_not_saved_as_supported(self):
        clean, problems = reading.validate_insights([insight(status="supported", source_refs=[ref(page=9)])], [block()])
        self.assertTrue(problems)
        self.assertEqual(clean, [])

    def test_merge_is_idempotent_and_preserves_different_exact_quotes(self):
        first = insight(status="supported")
        second = insight(source_refs=[ref(quote="a gain of 2%")])
        original = copy.deepcopy([first, second])
        merged = reading.merge_insights([first], [second])
        self.assertEqual(merged[0]["source_refs"], [ref(), ref(quote="a gain of 2%")])
        self.assertEqual(reading.merge_insights(merged, [first, second]), merged)
        self.assertEqual([first, second], original)

    def test_merge_does_not_promote_an_unquoted_supported_claim(self):
        incoming = insight(status="supported", source_refs=[{"page": 1, "block": "p1-1"}])
        self.assertEqual(reading.merge_insights([], [incoming])[0]["status"], "provisional")

    def test_dropping_pages_removes_stale_refs_and_reanchors_remaining_insights(self):
        raw = insight(status="supported", source_refs=[ref(), ref("p2-1", 2)])
        kept = reading.drop_insights_on_pages([raw], [1])
        self.assertEqual(kept[0]["source_refs"], [ref("p2-1", 2)])
        self.assertEqual((kept[0]["page"], kept[0]["block"]), (2, "p2-1"))
        self.assertEqual(kept[0]["status"], "provisional")
        self.assertEqual(raw["source_refs"], [ref(), ref("p2-1", 2)])


class ReadingStateAndGuideTest(unittest.TestCase):
    def test_page_state_records_schema_hash_and_actual_source_version(self):
        p = paper()
        reading.apply_reading_state(p, [1], {"1": "completed"}, True)
        state = p["reading"]["pages"]["1"]
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["source_version"], "source-1")
        self.assertTrue(state["zh_hash"])
        self.assertEqual(reading.pages_needing_reading(p), [])

    def test_skipped_and_old_version_pages_can_be_enriched(self):
        for status, version in (("skipped", 2), ("completed", 1), ("stale", 2), ("failed", 2)):
            with self.subTest(status=status, version=version):
                p = paper()
                reading.apply_reading_state(p, [1], status, True)
                p["reading"]["pages"]["1"]["version"] = version
                self.assertEqual(reading.pages_needing_reading(p), [1])

    def test_changed_translation_or_source_requires_enrichment(self):
        for changed in ("zh", "source"):
            with self.subTest(changed=changed):
                p = paper()
                reading.apply_reading_state(p, [1], "completed", True)
                if changed == "zh":
                    p["blocks"][0]["zh"] = "修改后的译文。"
                else:
                    p["translation"]["source_versions"]["1"] = "source-new"
                self.assertEqual(reading.pages_needing_reading(p), [1])
                reading.rebuild_guide(p)
                self.assertEqual(p["reading_guide"]["coverage"]["analyzed"], [])

    def test_full_text_missing_pages_is_never_complete(self):
        p = paper(total=3, done=(1,), target_pages=[1, 2, 3], requested_scope="all")
        reading.apply_reading_state(p, [1], "completed", True)
        reading.rebuild_guide(p)
        guide = p["reading_guide"]
        self.assertEqual(guide["status"], "partial")
        self.assertEqual(guide["coverage"]["total"], 3)
        self.assertFalse(guide["coverage"]["full_complete"])

    def test_page_holes_do_not_count_as_full_coverage(self):
        p = paper(total=3, done=(1, 2, 4), requested_scope="all")
        reading.apply_reading_state(p, [1, 2, 4], "completed", True)
        reading.rebuild_guide(p)
        self.assertNotEqual(p["reading_guide"]["status"], "complete")

    def test_unknown_total_does_not_claim_full_completion(self):
        p = paper(total=0)
        reading.apply_reading_state(p, [1], "completed", True)
        reading.rebuild_guide(p)
        self.assertNotEqual(p["reading_guide"]["status"], "complete")

    def test_partial_body_can_complete_its_explicit_range(self):
        p = paper(total=5, done=(2, 3), target_pages=[2, 3], requested_scope="body")
        reading.apply_reading_state(p, [2, 3], "completed", True)
        reading.rebuild_guide(p)
        guide = p["reading_guide"]
        self.assertEqual(guide["status"], "complete")
        self.assertEqual(guide["coverage"]["target_pages"], [2, 3])
        self.assertEqual(guide["coverage"]["excluded_pages"], [1, 4, 5])
        self.assertEqual(guide["coverage"]["scope"], "body")
        self.assertTrue(guide["coverage"]["range_complete"])
        self.assertFalse(guide["coverage"]["full_complete"])

    def test_real_total_uses_page_metadata_when_page_count_is_missing(self):
        p = paper(total=0)
        p["meta"]["pages"] = [{"n": 1}, {"n": 2}, {"n": 3}]
        reading.apply_reading_state(p, [1], "completed", True)
        reading.rebuild_guide(p)
        self.assertEqual(p["reading_guide"]["coverage"]["total"], 3)
        self.assertEqual(p["reading_guide"]["status"], "partial")


class SynthesisValidationTest(unittest.TestCase):
    def setUp(self):
        self.p = paper(total=2, done=(1, 2))
        self.p["reading"]["insights"] = [insight(), insight("i2", "p2-1", 2, type="evidence", content="提高2%。")]

    def test_legacy_statuses_cannot_assert_supported_without_evidence(self):
        self.p["reading"]["insights"][0]["source_refs"] = [{"page": 1, "block": "p1-1"}]
        reading.apply_synthesis(self.p, "没有出处的总结。", [{"from": "i1", "to": "i2", "content": "支撑"}],
                                {"i1": "supported", "i2": "invented"})
        self.assertEqual(self.p["reading"]["insights"][0]["status"], "provisional")
        self.assertEqual(self.p["reading"]["insights"][1]["status"], "provisional")
        guide = self.p.get("reading_guide") or {}
        self.assertFalse(guide.get("summary"))
        self.assertFalse(guide.get("relations"))
        self.assertFalse(guide.get("synth_of"))

    def test_only_relations_and_summaries_with_valid_refs_are_saved(self):
        relations = [{"from": "i1", "to": "i2", "content": "实验支持方法。", "status": "provisional",
                      "source_refs": [ref(), ref("p2-1", 2)]},
                     {"from": "i1", "to": "i2", "content": "虚构", "source_refs": [ref(quote="invented")]},
                     {"from": "missing", "to": "i2", "content": "错误端点", "source_refs": [ref()]}]
        reading.apply_synthesis(self.p, "方法有实验支持。", relations, {}, summary_refs=[ref()],
                                expected_hash=reading.insights_hash(self.p))
        guide = self.p["reading_guide"]
        self.assertEqual(guide["summary"], "方法有实验支持。")
        self.assertEqual(guide["summary_refs"], [ref()])
        self.assertEqual(guide["relations"], relations[:1])
        self.assertEqual(guide["synth_of"], reading.insights_hash(self.p))

    def test_relation_refs_must_cover_both_endpoint_sources(self):
        for source_refs in ([ref()], [ref("p2-1", 2)], [ref("p3-1", 3)]):
            with self.subTest(source_refs=source_refs):
                p = copy.deepcopy(self.p)
                p["blocks"].append(block("p3-1", 3))
                relation = {"from": "i1", "to": "i2", "content": "实验支持方法。", "source_refs": source_refs}
                reading.apply_synthesis(p, "", [relation], [])
                self.assertEqual(p["reading_guide"]["relations"], [])
                self.assertFalse(p["reading_guide"].get("synth_of"))

    def test_relation_status_defaults_to_provisional_and_preserves_valid_enum(self):
        for status in (None, "provisional", "supported", "unsupported"):
            with self.subTest(status=status):
                p = copy.deepcopy(self.p)
                relation = {"from": "i1", "to": "i2", "content": "实验支持方法。",
                            "source_refs": [ref(), ref("p2-1", 2)]}
                if status is not None:
                    relation["status"] = status
                reading.apply_synthesis(p, "", [relation], [])
                self.assertEqual(p["reading_guide"]["relations"][0]["status"], status or "provisional")

    def test_invalid_relation_status_cannot_mark_guide_verified(self):
        relation = {"from": "i1", "to": "i2", "content": "实验支持方法。", "status": "verified",
                    "source_refs": [ref(), ref("p2-1", 2)]}
        reading.apply_synthesis(self.p, "", [relation], [])
        self.assertEqual(self.p["reading_guide"]["relations"], [])
        self.assertFalse(self.p["reading_guide"].get("synth_of"))

    def test_all_invalid_outputs_leave_synthesis_retryable(self):
        reading.apply_synthesis(self.p, "无据总结。", [{"from": "i1", "to": "i1", "content": "自己支撑自己",
                                                         "source_refs": [ref()]}],
                                [{"insight": "i1", "status": "supported", "source_refs": [ref(quote="invented")]}],
                                summary_refs=[ref(quote="invented")])
        guide = self.p["reading_guide"]
        self.assertFalse(guide.get("summary"))
        self.assertFalse(guide.get("relations"))
        self.assertFalse(guide.get("checks"))
        self.assertFalse(guide.get("synth_of"))

    def test_checks_promote_only_valid_evidence_and_preserve_exact_refs(self):
        checks = [{"insight": "i1", "status": "supported", "source_refs": [ref(quote="We propose")]},
                  {"insight": "i2", "status": "supported", "source_refs": [ref("p2-1", 2, "invented")]}]
        reading.apply_synthesis(self.p, "", [], checks)
        first, second = self.p["reading"]["insights"]
        self.assertEqual(first["status"], "supported")
        self.assertEqual(first["source_refs"], [ref(quote="We propose")])
        self.assertEqual(second["status"], "provisional")
        before = copy.deepcopy(self.p["reading"]["insights"])
        reading.apply_synthesis(self.p, "", [], checks)
        self.assertEqual(self.p["reading"]["insights"], before)

    def test_legacy_status_dict_can_use_existing_valid_evidence(self):
        reading.apply_synthesis(self.p, "", [], {"i1": "supported"})
        self.assertEqual(self.p["reading"]["insights"][0]["status"], "supported")

    def test_outdated_synthesis_is_rejected_without_any_mutation(self):
        before = copy.deepcopy(self.p)
        result = reading.apply_synthesis(self.p, "旧总结", [], {"i1": "unsupported"},
                                         summary_refs=[ref()], expected_hash="old-snapshot")
        self.assertEqual(result, {"stale": True})
        self.assertEqual(self.p, before)

    def test_changed_insights_invalidate_previous_synthesis(self):
        reading.apply_synthesis(self.p, "方法有实验支持。", [], {}, summary_refs=[ref()])
        self.p["reading"]["insights"].append(insight("i3", content="新问题。", type="problem"))
        reading.rebuild_guide(self.p)
        self.assertNotIn("summary", self.p["reading_guide"])
        self.assertNotIn("summary_refs", self.p["reading_guide"])
        self.assertNotIn("synth_of", self.p["reading_guide"])


class PaperMergeValidationTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="easyread-reading-validation-")
        self.addCleanup(self.tmp.cleanup)
        self.ws = Workspace(Path(self.tmp.name))
        p = paper(total=2, done=())
        p["translation"].update(target_pages=[1, 2], requested_scope="all", source_versions={"1": "src1", "2": "src2"})
        write_json_atomic(self.ws.paper_path, p)

    def test_guard_rejects_before_any_merge_mutation(self):
        before = self.ws.load("paper")
        result = merge_blocks(self.ws, {"blocks": [block()], "meta": {"title_en": "stale"},
                                       "translation": {"page_revisions": {"1": "old"}}},
                              done=[1], replace_pages=[1], reading={"pages": [1], "insights": [insight()]},
                              guard=lambda p: False)
        self.assertEqual(result, {"stale": True})
        self.assertEqual(self.ws.load("paper"), before)

    def test_each_merge_updates_guide_after_done_pages_are_added(self):
        for page_number, expected_status in ((1, "partial"), (2, "complete")):
            merge_blocks(self.ws, {"blocks": [block(f"p{page_number}-1", page_number)]}, done=[page_number],
                         reading={"pages": [page_number], "status": "completed"})
            guide = self.ws.load("paper")["reading_guide"]
            self.assertEqual(guide["coverage"]["analyzed"], list(range(1, page_number + 1)))
            self.assertEqual(guide["status"], expected_status)

    def test_manual_edit_changes_revision_and_discards_stale_insight_and_guide_refs(self):
        merge_blocks(self.ws, {"blocks": [block()]}, done=[1],
                     reading={"pages": [1], "status": "completed", "insights": [insight()]})
        def old_task(p):
            p["translation"]["page_revisions"] = {"1": "in-flight"}
            reading.apply_synthesis(p, "旧总结。", [], {}, summary_refs=[ref()])
        self.ws.update("paper", old_task)
        set_block_text(self.ws, "p1-1", "修改后的新方法。")
        current = self.ws.load("paper")
        revision = current["translation"]["page_revisions"]["1"]
        self.assertNotEqual(revision, "in-flight")
        uuid.UUID(revision)
        self.assertEqual(current["reading"]["insights"], [])
        self.assertEqual(current["reading"]["pages"]["1"]["status"], "stale")
        self.assertFalse(current["reading_guide"].get("summary"))
        self.assertEqual(current["reading_guide"]["methods"], [])
        result = merge_blocks(self.ws, {"blocks": [block(zh="旧任务译文。")]},
                              guard=lambda p: p["translation"]["page_revisions"]["1"] == "in-flight")
        self.assertEqual(result, {"stale": True})
        self.assertEqual(self.ws.load("paper")["blocks"][0]["zh"], "修改后的新方法。")

    def test_replacement_without_reading_payload_discards_old_references(self):
        merge_blocks(self.ws, {"blocks": [block()]}, done=[1],
                     reading={"pages": [1], "status": "completed", "insights": [insight()]})
        merge_blocks(self.ws, {"blocks": [block(zh="重译后的新方法。")]}, replace_pages=[1])
        current = self.ws.load("paper")
        self.assertEqual(current["reading"]["insights"], [])
        self.assertEqual(current["reading"]["pages"]["1"]["status"], "stale")
        self.assertEqual(current["reading_guide"]["coverage"]["analyzed"], [])


if __name__ == "__main__":
    unittest.main()
